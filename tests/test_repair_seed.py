from sitewalk.__main__ import init
from sitewalk.repairs import register
from sitewalk.store import connect
from pathlib import Path


def test_repair_case_keeps_real_problem_snapshot(tmp_path):
    db = tmp_path / 'site.db'
    init(db)
    con = connect(db)
    with con:
        for point in 'BCD':
            con.execute('INSERT INTO point VALUES (?,?,?,?,?,?,?)',
                        (point, 'p1', 30, 120, 50, 0, ord(point) - 64))
        for point in 'ABCD':
            con.execute('INSERT INTO problem VALUES (?,?,?,?)',
                        ('P' + point, point, 'found', 'synthetic defect ' + point))
    con.close()
    contract = Path(__file__).parents[1] / 'fixtures' / 'repair-contract.json'
    assert register(db, contract) == 'site-repair-2026-03-03'
    con = connect(db)
    case = con.execute('SELECT * FROM repair_case').fetchone()
    assert case['contract_sha256'] and case['problem_digest']
    assert con.execute('SELECT count(*) FROM problem').fetchone()[0] == 4
    con.close()
