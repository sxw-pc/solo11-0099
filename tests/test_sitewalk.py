import json
from sitewalk.__main__ import demo,init
from sitewalk.ingest import photos,tracks
from sitewalk.rules import evaluate,advance_problem
from sitewalk.store import connect
def test_demo(tmp_path):
    report=json.loads(demo(tmp_path).read_text(encoding='utf-8'))
    assert report['decision'][0]['result']=='覆盖'
    assert report['source'][0]['sha256']
def test_missing_track(tmp_path):
    db=tmp_path/'x.db';init(db)
    assert evaluate(db)['A']=='待复核'
def test_problem_transition(tmp_path):
    db=tmp_path/'x.db';init(db);con=connect(db)
    with con:con.execute('INSERT INTO problem VALUES (?,?,?,?)',('p','A','found','e'))
    advance_problem(db,'p','assigned','new')
    assert connect(db).execute('SELECT state FROM problem').fetchone()[0]=='assigned'
def test_duplicate_photo_file(tmp_path):
    db=tmp_path/'x.db';init(db)
    (tmp_path/'p.txt').write_text('x');file=tmp_path/'p.jsonl';file.write_text(json.dumps({'point_id':'A','ts':'2026-01-01T00:00:00Z','file':'p.txt'}))
    assert photos(file,db)==1
    assert photos(file,db)==0
