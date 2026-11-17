from pathlib import Path
import json
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


def test_fixture_exposes_a_greedy_failure():
    fixture=Path(__file__).parents[1]/'fixtures'/'rebar-greedy-trap.json'
    data=json.loads(fixture.read_text(encoding='utf-8'))
    assert sorted(sum(data['optimal_patterns'],[]))==sorted(data['demand_lengths'])
    assert all(sum(pattern)<=10 for pattern in data['optimal_patterns'])
    assert len(data['optimal_patterns'])==2
    assert len(data['first_fit_decreasing_patterns'])==3
