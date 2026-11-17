"""Contract input for planning repairs of recorded inspection problems.

The seed registers a case; assignment and scheduling are intentionally absent.
"""
import json
from hashlib import sha256
from pathlib import Path

from .store import connect


def register(db_path, contract_path):
    raw = Path(contract_path).read_bytes()
    data = json.loads(raw)
    con = connect(db_path)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS repair_case(
      id TEXT PRIMARY KEY, contract_sha256 TEXT NOT NULL, contract_json TEXT NOT NULL,
      problem_digest TEXT NOT NULL
    );
    ''')
    problems = {r['id']: dict(r) for r in con.execute('SELECT * FROM problem')}
    if any(j['problem_id'] not in problems for j in data['jobs']):
        raise ValueError('unknown_problem')
    if any(problems[j['problem_id']]['state'] != 'found' for j in data['jobs']):
        raise ValueError('problem_not_found')
    snapshot = [problems[j['problem_id']] for j in data['jobs']]
    digest = sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with con:
        con.execute('INSERT INTO repair_case VALUES (?,?,?,?)',
                    (data['case_id'], sha256(raw).hexdigest(), raw.decode(), digest))
    con.close()
    return data['case_id']
