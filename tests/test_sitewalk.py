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


def test_track_gap_is_not_interpolated():
    from sitewalk.rules import location
    from sitewalk.ingest import time
    track=[{'ts':'2026-01-01T00:00:00Z','lat':30,'lon':120},{'ts':'2026-01-01T01:00:00Z','lat':31,'lon':121}]
    assert location(track,time('2026-01-01T00:30:00Z')) is None
def test_item_without_evidence(tmp_path):
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:c.execute('INSERT INTO item VALUES (?,?,?,?)',('i','A',None,None))
    assert evaluate(db)['A']=='待复核'
    assert c.execute("SELECT count(*) FROM issue WHERE code='missing_item'").fetchone()[0]
def test_plan_time(tmp_path):
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:
        c.execute("INSERT INTO source(path,sha256) VALUES ('x','h')")
        c.execute("INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (1,'A','2028-01-01T00:00:00Z','ph','{}')")
    evaluate(db)
    assert c.execute("SELECT count(*) FROM issue WHERE code='plan_time'").fetchone()[0]
def test_invalid_problem_transition(tmp_path):
    import pytest
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:c.execute('INSERT INTO problem VALUES (?,?,?,?)',('p','A','found','e'))
    with pytest.raises(ValueError,match='illegal_problem_transition'):advance_problem(db,'p','closed','e')
def test_reused_photo_across_points(tmp_path):
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:
        c.execute('INSERT INTO point VALUES (?,?,?,?,?,?,?)',('B','p1',30,120,50,0,2))
        c.execute("INSERT INTO source(path,sha256) VALUES ('x','h')")
        c.execute("INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (1,'A','2026-01-01T00:00:00Z','same','{}')")
        c.execute("INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (1,'B','2026-01-01T00:01:00Z','same','{}')")
    evaluate(db)
    assert c.execute("SELECT count(*) FROM issue WHERE code='reused_photo'").fetchone()[0]

def test_plan_load_transaction(tmp_path):
    from sitewalk.plan import load_plan
    import pytest
    db=tmp_path/'x.db';p=tmp_path/'plan.json'
    data={'plan':{'id':'p','version':1,'valid_from':'2026-01-01T00:00:00Z','valid_to':'2027-01-01T00:00:00Z'},'points':[{'id':'A','lat':30,'lon':120,'radius_m':20,'min_dwell_s':10,'order':1}]}
    p.write_text(json.dumps(data))
    assert load_plan(p,db)==1
    with pytest.raises(ValueError,match='plan_immutable'):load_plan(p,db)
    assert connect(db).execute('SELECT count(*) FROM point').fetchone()[0]==1

def test_demo_repeat_is_deterministic(tmp_path):
    first=demo(tmp_path).read_bytes()
    second=demo(tmp_path).read_bytes()
    assert first==second

def test_interpolation_midpoint():
    from sitewalk.rules import location
    from sitewalk.ingest import time
    track=[{'ts':'2026-01-01T00:00:00Z','lat':30,'lon':120},{'ts':'2026-01-01T00:10:00Z','lat':32,'lon':122}]
    assert location(track,time('2026-01-01T00:05:00Z'))==(31,121)
def test_dwell_requirement_marks_uncertain(tmp_path):
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:c.execute("UPDATE point SET min_dwell_s=600 WHERE id='A'")
    assert evaluate(db)['A']=='待复核'
def test_photo_asset_is_unique(tmp_path):
    db=tmp_path/'x.db';init(db)
    (tmp_path/'p.txt').write_text('x')
    f=tmp_path/'photos.jsonl';f.write_text('\n'.join(json.dumps({'point_id':'A','ts':f'2026-01-01T00:0{i}:00Z','file':'p.txt'}) for i in (1,2)))
    assert photos(f,db)==2
    assert connect(db).execute('SELECT count(*) FROM asset').fetchone()[0]==1
def test_markdown_report_exists(tmp_path):
    assert demo(tmp_path).with_suffix('.md').exists()
def test_missing_photo_never_covered(tmp_path):
    db=tmp_path/'x.db';init(db)
    assert evaluate(db)['A']=='待复核'

def test_evidence_and_run_are_persisted(tmp_path):
    data=json.loads(demo(tmp_path).read_text(encoding='utf-8'))
    assert data["run"]
    assert data['evidence']
    assert all(e['run_id'] for e in data['evidence'])

def test_valid_item_photo_link(tmp_path):
    db=tmp_path/'x.db';init(db)
    (tmp_path/'p.txt').write_text('x');f=tmp_path/'p.jsonl';f.write_text(json.dumps({'point_id':'A','ts':'2026-01-01T00:05:00Z','file':'p.txt'}));photos(f,db)
    h=connect(db).execute('SELECT sha256 FROM photo').fetchone()[0]
    c=connect(db)
    with c:c.execute('INSERT INTO item VALUES (?,?,?,?)',('i','A',h,None))
    evaluate(db)
    assert c.execute("SELECT count(*) FROM issue WHERE code='missing_item'").fetchone()[0]==0
def test_route_order_reversal(tmp_path):
    db=tmp_path/'x.db';init(db);c=connect(db)
    with c:
        c.execute('INSERT INTO point VALUES (?,?,?,?,?,?,?)',('B','p1',30,120,50,0,2))
        c.execute("INSERT INTO source(path,sha256) VALUES ('x','h')")
        c.execute("INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (1,'A','2026-01-01T00:05:00Z','a','{}')")
        c.execute("INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (1,'B','2026-01-01T00:04:00Z','b','{}')")
        c.execute("INSERT INTO track(source_id,ts,lat,lon) VALUES (1,'2026-01-01T00:00:00Z',30,120)")
        c.execute("INSERT INTO track(source_id,ts,lat,lon) VALUES (1,'2026-01-01T00:10:00Z',30,120)")
    evaluate(db)
    assert c.execute("SELECT count(*) FROM issue WHERE code='route_order'").fetchone()[0]==1
def test_decision_links_run_and_evidence(tmp_path):
    data=json.loads(demo(tmp_path).read_text(encoding='utf-8'))
    assert data['decision'][0]['run_id']==data['run'][0]['id']
    assert data['evidence']
