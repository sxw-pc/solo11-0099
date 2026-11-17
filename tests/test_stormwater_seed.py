from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.stormwater import register
from sitewalk.store import connect


def test_stormwater_case_binds_site_and_unrouted_rain(tmp_path):
    db=tmp_path/'site.db'
    init(db)
    fixture=Path(__file__).parents[1]/'fixtures'/'stormwater-contract.json'
    assert register(db,fixture)=='rain-pad-A-2026-04-10'
    con=connect(db)
    row=con.execute('SELECT * FROM stormwater_case').fetchone()
    assert row['plan_id']=='p1' and row['source_sha256']
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0]==0
    con.close()
