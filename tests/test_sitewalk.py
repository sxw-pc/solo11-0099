import json

import pytest

from sitewalk.__main__ import demo,init
from sitewalk.ingest import items,photos,tracks
from sitewalk.plan import load_plan,verify_plan
from sitewalk.rules import advance_problem,evaluate,route_report
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

def test_raw_source_is_retained_after_input_changes(tmp_path):
    path=demo(tmp_path)
    before=path.read_bytes()
    db=tmp_path/"sitewalk.db"
    con=connect(db)
    row=con.execute("SELECT path,raw FROM source LIMIT 1").fetchone() if "source"!='source_file' else con.execute('SELECT name AS path,raw FROM source_file LIMIT 1').fetchone()
    from pathlib import Path
    preserved=bytes(row['raw'])
    Path(row['path']).write_bytes(b'changed after import')
    assert bytes(con.execute("SELECT raw FROM source LIMIT 1").fetchone()[0])==preserved
    assert before and preserved

def test_batch_links_source_and_plan(tmp_path):
    data=json.loads(demo(tmp_path).read_text(encoding='utf-8'))
    assert data['batch'][0]['plan_id']==data['plan'][0]['id']
    assert all(src['batch_id']==data['batch'][0]['id'] for src in data['source'])


# --- prerequisite route contracts ------------------------------------------
POINTS = [
    {'id': 'A', 'lat': 30.010, 'lon': 120.0, 'radius_m': 50, 'min_dwell_s': 300, 'ord': 1},
    {'id': 'B', 'lat': 30.000, 'lon': 120.0, 'radius_m': 50, 'min_dwell_s': 300, 'ord': 2},
    {'id': 'C', 'lat': 30.020, 'lon': 120.0, 'radius_m': 50, 'min_dwell_s': 300, 'ord': 3},
]
DETOUR_TRACK = (
    'ts,lat,lon\n'
    '2026-01-01T00:00:00Z,30.000,120\n'
    '2026-01-01T00:10:00Z,30.000,120\n'
    '2026-01-01T00:11:00Z,30.005,120\n'
    '2026-01-01T00:20:00Z,30.010,120\n'
    '2026-01-01T00:30:00Z,30.010,120\n'
    '2026-01-01T00:31:00Z,30.015,120\n'
    '2026-01-01T00:40:00Z,30.020,120\n'
    '2026-01-01T00:50:00Z,30.020,120\n'
)
DETOUR_PHOTOS = [
    {'point_id': 'B', 'ts': '2026-01-01T00:05:00Z'},
    {'point_id': 'A', 'ts': '2026-01-01T00:25:00Z'},
    {'point_id': 'C', 'ts': '2026-01-01T00:45:00Z'},
]
REVERSED_TRACK = (
    'ts,lat,lon\n'
    '2026-01-01T00:00:00Z,30.020,120\n'
    '2026-01-01T00:10:00Z,30.020,120\n'
    '2026-01-01T00:11:00Z,30.015,120\n'
    '2026-01-01T00:20:00Z,30.010,120\n'
    '2026-01-01T00:30:00Z,30.010,120\n'
    '2026-01-01T00:31:00Z,30.005,120\n'
    '2026-01-01T00:40:00Z,30.000,120\n'
    '2026-01-01T00:50:00Z,30.000,120\n'
)
REVERSED_PHOTOS = [
    {'point_id': 'C', 'ts': '2026-01-01T00:05:00Z'},
    {'point_id': 'A', 'ts': '2026-01-01T00:25:00Z'},
    {'point_id': 'B', 'ts': '2026-01-01T00:45:00Z'},
]


def _plan_doc(contract='dependency', edges=(('B', 'C'),), points=None, version=1, plan_id='p1'):
    return {
        'plan': {
            'id': plan_id, 'version': version,
            'valid_from': '2026-01-01T00:00:00Z', 'valid_to': '2027-01-01T00:00:00Z',
            'route_contract': contract,
        },
        'prerequisites': [list(edge) for edge in edges],
        'points': points if points is not None else [dict(p) for p in POINTS],
    }


def _scenario(tmp_path, contract='dependency', photo_rows=None, track=None, edges=(('B', 'C'),),
              points=None, photo_version=None, reuse_file=False, write_items=True):
    db = tmp_path / 'site.db'
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(_plan_doc(contract, edges, points)), encoding='utf-8')
    load_plan(plan_path, db)

    photo_rows = photo_rows if photo_rows is not None else DETOUR_PHOTOS
    lines = []
    for photo in photo_rows:
        name = 'shared.jpg' if reuse_file else f"{photo['point_id']}.jpg"
        file = tmp_path / name
        if not file.exists():
            file.write_text('synthetic bytes for ' + (name if not reuse_file else 'all'), encoding='utf-8')
        row = {'point_id': photo['point_id'], 'ts': photo['ts'], 'file': name}
        if photo_version is not None:
            row['plan_version'] = photo_version
        lines.append(json.dumps(row))
    photo_file = tmp_path / 'photos.jsonl'
    photo_file.write_text('\n'.join(lines), encoding='utf-8')
    photos(photo_file, db)

    track_file = tmp_path / 'track.csv'
    track_file.write_text(track if track is not None else DETOUR_TRACK, encoding='utf-8')
    tracks(track_file, db)

    if write_items:
        con = connect(db)
        hashes = {
            row['point_id']: row['sha256']
            for row in con.execute('SELECT point_id,sha256 FROM photo')
        }
        con.close()
        item_rows = [
            {'id': 'item-' + pid, 'point_id': pid, 'photo_hash': hashes[pid], 'note': None}
            for pid in sorted(hashes)
        ]
        item_file = tmp_path / 'items.json'
        item_file.write_text(json.dumps(item_rows), encoding='utf-8')
        items(item_file, db)
    return db


def _verdict(db, contract):
    return next(v for v in route_report(db) if v['contract'] == contract)


def test_dependency_plan_accepts_closure_detour(tmp_path):
    db = _scenario(tmp_path)
    dependency = evaluate(db, route_contract='dependency')
    assert dependency == {'A': '覆盖', 'B': '覆盖', 'C': '覆盖'}
    verdict = _verdict(db, 'dependency')
    assert verdict['accepted'] is True
    assert verdict['arrival_order'] == ['B', 'A', 'C']
    assert verdict['violated_edges'] == []
    assert verdict['missing_points'] == []
    assert verdict['evidence_hash']

    con = connect(db)
    decisions = {row['point_id']: row for row in con.execute('SELECT * FROM decision')}
    assert set(decisions) == {'A', 'B', 'C'}
    assert decisions['B']['arrival_order'] == '["B", "A", "C"]'
    assert decisions['B']['track_interval'] == '["2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"]'
    assert decisions['A']['track_interval'] == '["2026-01-01T00:20:00Z", "2026-01-01T00:30:00Z"]'
    assert all(row['evidence_hash'] for row in decisions.values())
    assert all(row['route_contract'] == 'dependency' for row in decisions.values())
    con.close()


def test_fixed_order_contract_flags_closure_detour(tmp_path):
    db = _scenario(tmp_path)
    fixed = evaluate(db, route_contract='fixed_order')
    assert fixed['B'] == '待复核' and fixed['A'] == '待复核'
    verdict = _verdict(db, 'fixed_order')
    assert verdict['accepted'] is False
    assert verdict['result'] == '待复核'
    assert verdict['arrival_order'] == ['B', 'A', 'C']
    assert ['A', 'B'] in verdict['violated_edges']
    con = connect(db)
    assert con.execute("SELECT count(*) FROM issue WHERE code='route_order'").fetchone()[0] == 1
    con.close()


def test_dependency_edge_violation_rejects_both_contracts(tmp_path):
    db = _scenario(tmp_path, photo_rows=REVERSED_PHOTOS, track=REVERSED_TRACK)
    assert evaluate(db, route_contract='dependency') != {'A': '覆盖', 'B': '覆盖', 'C': '覆盖'}
    dependency = _verdict(db, 'dependency')
    assert dependency['arrival_order'] == ['C', 'A', 'B']
    assert dependency['violated_edges'] == [['B', 'C']]
    assert dependency['accepted'] is False

    evaluate(db, route_contract='fixed_order')
    fixed = _verdict(db, 'fixed_order')
    assert fixed['accepted'] is False
    assert ['B', 'C'] in fixed['violated_edges']


def test_dependency_cycle_is_rejected_without_partial_plan(tmp_path):
    db = tmp_path / 'site.db'
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(_plan_doc(edges=(('A', 'B'), ('B', 'A')))), encoding='utf-8')
    with pytest.raises(ValueError, match='dependency_cycle'):
        load_plan(path, db)
    con = connect(db)
    assert con.execute('SELECT count(*) FROM plan').fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM point').fetchone()[0] == 0
    con.close()


def test_unknown_prerequisite_point_is_rejected(tmp_path):
    db = tmp_path / 'site.db'
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(_plan_doc(edges=(('B', 'Z'),))), encoding='utf-8')
    with pytest.raises(ValueError, match='unknown_prerequisite_point'):
        load_plan(path, db)


def test_dependency_contract_does_not_accept_missing_point(tmp_path):
    db = _scenario(tmp_path, photo_rows=[p for p in DETOUR_PHOTOS if p['point_id'] != 'A'])
    result = evaluate(db, route_contract='dependency')
    assert result['A'] == '待复核'
    verdict = _verdict(db, 'dependency')
    assert verdict['accepted'] is False
    assert 'A' in verdict['missing_points']


def test_track_gap_not_masked_by_dependency_contract(tmp_path):
    gap_track = (
        'ts,lat,lon\n'
        '2026-01-01T00:00:00Z,30.000,120\n'
        '2026-01-01T00:10:00Z,30.000,120\n'
        '2026-01-01T00:40:00Z,30.010,120\n'
        '2026-01-01T00:50:00Z,30.010,120\n'
        '2026-01-01T01:00:00Z,30.020,120\n'
        '2026-01-01T01:10:00Z,30.020,120\n'
    )
    gap_photos = [
        {'point_id': 'B', 'ts': '2026-01-01T00:05:00Z'},
        {'point_id': 'A', 'ts': '2026-01-01T00:25:00Z'},
        {'point_id': 'C', 'ts': '2026-01-01T01:05:00Z'},
    ]
    db = _scenario(tmp_path, photo_rows=gap_photos, track=gap_track)
    result = evaluate(db, route_contract='dependency')
    assert result['A'] == '待复核'
    con = connect(db)
    assert con.execute("SELECT count(*) FROM issue WHERE code='track_gap'").fetchone()[0] >= 1
    con.close()
    assert _verdict(db, 'dependency')['accepted'] is False


def test_reused_photo_not_masked_by_dependency_contract(tmp_path):
    db = _scenario(tmp_path, reuse_file=True)
    result = evaluate(db, route_contract='dependency')
    assert result['B'] == '待复核' and result['C'] == '待复核'
    con = connect(db)
    assert con.execute("SELECT count(*) FROM issue WHERE code='reused_photo'").fetchone()[0] >= 1
    con.close()
    assert _verdict(db, 'dependency')['accepted'] is False


def test_wrong_plan_version_not_masked_by_dependency_contract(tmp_path):
    db = _scenario(tmp_path, photo_version=99)
    result = evaluate(db, route_contract='dependency')
    assert set(result.values()) == {'待复核'}
    con = connect(db)
    assert con.execute("SELECT count(*) FROM issue WHERE code='plan_version'").fetchone()[0] == 3
    con.close()
    assert _verdict(db, 'dependency')['accepted'] is False


def test_plan_version_and_graph_are_frozen(tmp_path):
    db = tmp_path / 'site.db'
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(_plan_doc(contract='fixed_order')), encoding='utf-8')
    assert load_plan(path, db) == 3
    assert verify_plan(db) == {'p1': True}
    con = connect(db)
    assert con.execute('SELECT route_contract FROM plan').fetchone()[0] == 'fixed_order'
    assert [tuple(r) for r in con.execute('SELECT before,after FROM prerequisite')] == [('B', 'C')]
    con.close()

    newer = _plan_doc(contract='fixed_order', version=2)
    path.write_text(json.dumps(newer), encoding='utf-8')
    with pytest.raises(ValueError, match='plan_immutable'):
        load_plan(path, db)

    tampered = connect(db)
    with tampered:
        tampered.execute("UPDATE point SET ord=9 WHERE id='A'")
        tampered.execute("DELETE FROM prerequisite")
    assert verify_plan(db) == {'p1': False}
    tampered.close()


def test_dependency_digest_changes_with_edges(tmp_path):
    first = tmp_path / 'first.json'
    second = tmp_path / 'second.json'
    first.write_text(json.dumps(_plan_doc(edges=(('B', 'C'),))), encoding='utf-8')
    second.write_text(json.dumps(_plan_doc(edges=(('A', 'C'),))), encoding='utf-8')
    load_plan(first, tmp_path / 'first.db')
    load_plan(second, tmp_path / 'second.db')
    a = connect(tmp_path / 'first.db')
    b = connect(tmp_path / 'second.db')
    assert a.execute('SELECT digest FROM plan').fetchone()[0] != b.execute('SELECT digest FROM plan').fetchone()[0]
    a.close(); b.close()


def test_safety_checkpoint_prerequisite_ab_rejects_detour(tmp_path):
    # The supervisor can express "check safety point A before B".  The same
    # detour that is legal under the fixture edge B->C must now reject.
    db = _scenario(tmp_path, edges=(('A', 'B'),))
    assert evaluate(db, route_contract='dependency')['B'] == '待复核'
    verdict = _verdict(db, 'dependency')
    assert verdict['arrival_order'] == ['B', 'A', 'C']
    assert verdict['violated_edges'] == [['A', 'B']]
    assert verdict['accepted'] is False


def test_fixed_order_plan_rejects_conflicting_prerequisite(tmp_path):
    db = tmp_path / 'site.db'
    path = tmp_path / 'plan.json'
    # B(ord 2) -> A(ord 1) is unsatisfiable under a fixed-order contract.
    path.write_text(json.dumps(_plan_doc(contract='fixed_order', edges=(('B', 'A'),))), encoding='utf-8')
    with pytest.raises(ValueError, match='prerequisite_conflicts_with_order'):
        load_plan(path, db)


def test_dependency_contract_still_enforces_dwell_threshold(tmp_path):
    points = [dict(p) for p in POINTS]
    points[1]['min_dwell_s'] = 3600  # B: only 10 minutes present in the fixture
    db = _scenario(tmp_path, points=points)
    result = evaluate(db)
    assert result['B'] == '待复核'
    con = connect(db)
    assert con.execute(
        "SELECT count(*) FROM issue WHERE code='insufficient_dwell' AND detail='B'"
    ).fetchone()[0] >= 1
    con.close()
    verdict = _verdict(db, 'dependency')
    assert verdict['accepted'] is False
    assert 'B' in verdict['missing_points']


def test_dependency_contract_still_requires_item_evidence(tmp_path):
    db = _scenario(tmp_path, write_items=False)
    item_file = tmp_path / 'items.json'
    item_file.write_text(json.dumps([
        {'id': 'item-' + pid, 'point_id': pid, 'photo_hash': None, 'note': None}
        for pid in ('A', 'B', 'C')
    ]), encoding='utf-8')
    items(item_file, db)
    result = evaluate(db)
    assert set(result.values()) == {'待复核'}
    con = connect(db)
    assert con.execute(
        "SELECT count(*) FROM issue WHERE code='missing_item'"
    ).fetchone()[0] == 3
    con.close()
    assert _verdict(db, 'dependency')['accepted'] is False


def test_evaluate_uses_plan_declared_contract_by_default(tmp_path):
    db = _scenario(tmp_path)
    assert evaluate(db) == {'A': '覆盖', 'B': '覆盖', 'C': '覆盖'}
    con = connect(db)
    row = con.execute("SELECT route_contract FROM decision WHERE point_id='B'").fetchone()
    assert row[0] == 'dependency'
    con.close()


def test_route_block_spelling_and_point_after_edges(tmp_path):
    doc = _plan_doc(contract='dependency', edges=())
    doc['plan']['route'] = {'contract': 'dependency', 'prerequisites': [{'before': 'B', 'after': 'C'}]}
    for point in doc['points']:
        if point['id'] == 'C':
            point['after'] = ['B']
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    db = tmp_path / 'site.db'
    assert load_plan(path, db) == 3
    con = connect(db)
    edges = [tuple(r) for r in con.execute('SELECT before,after FROM prerequisite')]
    assert edges == [('B', 'C')]  # the duplicate spelling is deduplicated
    con.close()


def test_track_gap_inside_dwell_interval_is_not_dwell_evidence(tmp_path):
    gap_track = (
        'ts,lat,lon\n'
        '2026-01-01T00:00:00Z,30.000,120\n'
        '2026-01-01T00:40:00Z,30.000,120\n'
        '2026-01-01T00:41:00Z,30.010,120\n'
        '2026-01-01T00:50:00Z,30.010,120\n'
        '2026-01-01T00:51:00Z,30.020,120\n'
        '2026-01-01T01:00:00Z,30.020,120\n'
    )
    gap_photos = [
        {'point_id': 'B', 'ts': '2026-01-01T00:05:00Z'},
        {'point_id': 'A', 'ts': '2026-01-01T00:45:00Z'},
        {'point_id': 'C', 'ts': '2026-01-01T00:55:00Z'},
    ]
    db = _scenario(tmp_path, photo_rows=gap_photos, track=gap_track)
    assert evaluate(db)['B'] == '待复核'
    con = connect(db)
    codes = {row[0] for row in con.execute("SELECT code FROM issue WHERE detail='B'")}
    con.close()
    assert 'track_gap' in codes


def test_dependency_violation_reports_the_edge_not_only_order(tmp_path):
    db = _scenario(tmp_path, photo_rows=REVERSED_PHOTOS, track=REVERSED_TRACK)
    evaluate(db)
    con = connect(db)
    edge_issues = [json.loads(r[0]) for r in con.execute(
        "SELECT detail FROM issue WHERE code='route_edge'"
    )]
    order_issues = con.execute("SELECT count(*) FROM issue WHERE code='route_order'").fetchone()[0]
    con.close()
    assert edge_issues == [['B', 'C']]
    assert order_issues == 0


def test_contract_override_does_not_mutate_the_frozen_plan(tmp_path):
    db = _scenario(tmp_path)
    evaluate(db, route_contract='fixed_order')
    con = connect(db)
    assert con.execute("SELECT route_contract FROM plan").fetchone()[0] == 'dependency'
    assert con.execute("SELECT count(*) FROM point").fetchone()[0] == 3
    con.close()
    assert verify_plan(db) == {'p1': True}


def test_route_report_is_per_run(tmp_path):
    db = _scenario(tmp_path)
    evaluate(db)
    first = route_report(db)
    assert {v['contract'] for v in first} == {'fixed_order', 'dependency'}
    assert all(v['run_id'] == first[0]['run_id'] for v in first)
    # A second evaluation over unchanged input reuses the same run and keeps
    # the report stable instead of accumulating duplicate verdicts.
    evaluate(db)
    assert route_report(db) == first
