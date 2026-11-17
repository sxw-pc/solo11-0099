from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.concrete import register
from sitewalk.store import connect


def test_concrete_case_binds_real_site_point_without_patrol_decision(tmp_path):
    db=tmp_path/'site.db'
    init(db)
    fixture=Path(__file__).parents[1]/'fixtures'/'concrete-contract.json'
    assert register(db,fixture)=='mix-pad-A-2026-04-09'
    con=connect(db)
    row=con.execute('SELECT * FROM concrete_case').fetchone()
    assert row['plan_id']=='p1' and row['source_sha256']
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0]==0
    con.close()
