from pathlib import Path
from sitewalk.__main__ import init
from sitewalk.earthwork import register
from sitewalk.store import connect


def test_survey_freezes_real_site_anchor(tmp_path):
    db = tmp_path / 'site.db'
    init(db)
    survey = Path(__file__).parents[1] / 'fixtures' / 'earthwork-survey.json'
    assert register(db, survey) == 'pad-A-2026-04-02'
    con = connect(db)
    case = con.execute('SELECT * FROM earthwork_case').fetchone()
    assert case['plan_id'] == 'p1' and case['source_sha256']
    assert con.execute('SELECT count(*) FROM point WHERE id=?', ('A',)).fetchone()[0] == 1
    con.close()
