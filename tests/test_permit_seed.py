from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.permits import register
from sitewalk.store import connect


def test_permit_case_binds_site_zone_without_coverage_decision(tmp_path):
    db = tmp_path / 'site.db'
    init(db)
    contract = Path(__file__).parents[1] / 'fixtures' / 'access-contract.json'
    assert register(db, contract) == 'zone-A-2026-04-04'
    con = connect(db)
    case = con.execute('SELECT * FROM access_case').fetchone()
    assert case['plan_id'] == 'p1' and case['contract_sha256']
    assert con.execute('SELECT count(*) FROM decision').fetchone()[0] == 0
    con.close()
