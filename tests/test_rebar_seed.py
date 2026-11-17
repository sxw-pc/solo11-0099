from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.rebar import register
from sitewalk.store import connect


def test_rebar_case_binds_site_without_coverage_decisions(tmp_path):
    db=tmp_path/'site.db'
    init(db)
    contract=Path(__file__).parents[1]/'fixtures'/'rebar-contract.json'
    assert register(db,contract)=='rebar-pad-A-2026-04-06'
    con=connect(db)
    row=con.execute('SELECT * FROM rebar_case').fetchone()
    assert row['plan_id']=='p1' and row['contract_sha256']
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0]==0
    con.close()
