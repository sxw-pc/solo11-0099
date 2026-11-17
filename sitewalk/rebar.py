"""Intake of rebar stock and cut demand; no cutting pattern is calculated."""
import json
from hashlib import sha256
from pathlib import Path
from .store import connect


def register(db_path, contract_path):
    raw=Path(contract_path).read_bytes()
    contract=json.loads(raw)
    con=connect(db_path)
    con.executescript('''CREATE TABLE IF NOT EXISTS rebar_case(
      id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id),
      contract_sha256 TEXT NOT NULL, contract_json TEXT NOT NULL
    );''')
    if not con.execute('SELECT 1 FROM point WHERE id=? AND plan_id=?',
                       (contract['site_point'],contract['plan_id'])).fetchone():
        raise ValueError('unknown_site_point')
    with con:
        con.execute('INSERT INTO rebar_case VALUES (?,?,?,?)',
                    (contract['case_id'],contract['plan_id'],sha256(raw).hexdigest(),raw.decode()))
    con.close()
    return contract['case_id']
