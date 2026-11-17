"""Survey intake for earthwork quantities; does not reconstruct a surface."""
import json
from hashlib import sha256
from pathlib import Path
from .store import connect


def register(db_path, survey_path):
    raw = Path(survey_path).read_bytes()
    survey = json.loads(raw)
    con = connect(db_path)
    con.executescript('''CREATE TABLE IF NOT EXISTS earthwork_case(
      id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id),
      source_sha256 TEXT NOT NULL, survey_json TEXT NOT NULL
    );''')
    if not con.execute('SELECT 1 FROM point WHERE id=? AND plan_id=?',
                       (survey['anchor_point'], survey['plan_id'])).fetchone():
        raise ValueError('unknown_survey_anchor')
    with con:
        con.execute('INSERT INTO earthwork_case VALUES (?,?,?,?)',
                    (survey['case_id'], survey['plan_id'], sha256(raw).hexdigest(), raw.decode()))
    con.close()
    return survey['case_id']
