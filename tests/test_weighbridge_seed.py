import json
from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.weighbridge import register
from sitewalk.store import connect


def test_weighbridge_case_and_matching_trap(tmp_path):
    db=tmp_path/'site.db'
    init(db)
    fixtures=Path(__file__).parents[1]/'fixtures'
    assert register(db,fixtures/'weighbridge-contract.json')=='bridge-A-2026-04-08'
    trap=json.loads((fixtures/'matching-trap.json').read_text(encoding='utf-8'))
    assert len(trap['nearest_first_result'])==1
    assert len(trap['complete_matching'])==2
    con=connect(db)
    assert con.execute('SELECT source_sha256 FROM weigh_case').fetchone()[0]
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0]==0
    con.close()
