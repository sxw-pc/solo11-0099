import json
import math
from datetime import timedelta
from .ingest import time
from .store import connect
def distance(a,b,c,d):
    dy=(a-c)*111_000;dx=(b-d)*111_000*math.cos(math.radians((a+c)/2));return math.hypot(dx,dy)
def location(track,stamp,max_gap=timedelta(minutes=10)):
    before=[r for r in track if time(r['ts'])<=stamp];after=[r for r in track if time(r['ts'])>=stamp]
    if not before or not after:return None
    a=before[-1];b=after[0];ta=time(a['ts']);tb=time(b['ts'])
    if tb-ta>max_gap:return None
    fraction=0 if ta==tb else (stamp-ta)/(tb-ta)
    return a['lat']+(b['lat']-a['lat'])*fraction,a['lon']+(b['lon']-a['lon'])*fraction
def evaluate(db_path):
    con=connect(db_path);results={}
    with con:
        con.execute('DELETE FROM decision');con.execute("DELETE FROM issue WHERE code IN ('reused_photo','missing_item','route_order','insufficient_dwell','track_gap','plan_time','unresolved_problem')")
        track=[dict(r) for r in con.execute('SELECT * FROM track ORDER BY ts,id')]
        photos=[dict(r) for r in con.execute('SELECT * FROM photo ORDER BY ts,id')]
        reused={p['sha256'] for p in photos if len({(r['point_id'],r['ts'][:10]) for r in photos if r['sha256']==p['sha256']})>1}
        for h in reused:con.execute('INSERT INTO issue(code,detail) VALUES (?,?)',('reused_photo',h))
        prev_visit=None
        for point in con.execute('SELECT * FROM point ORDER BY ord'):
            plan=con.execute('SELECT * FROM plan WHERE id=?',(point['plan_id'],)).fetchone()
            attached=[p for p in photos if p['point_id']==point['id']]
            problems=[];coordinates=[];valid=[]
            for p in attached:
                stamp=time(p['ts'])
                if stamp<time(plan['valid_from']) or stamp>=time(plan['valid_to']):problems.append('plan_time');continue
                loc=location(track,stamp)
                if loc is None:problems.append('track_gap');continue
                coordinates.append(loc)
                if distance(*loc,point['lat'],point['lon'])<=point['radius_m']:valid.append(p)
            if len(valid)<1:problems.append('insufficient_dwell')
            elif point['min_dwell_s']>0:
                visits=[r for r in track if distance(r['lat'],r['lon'],point['lat'],point['lon'])<=point['radius_m']]
                if len(visits)<2 or (time(visits[-1]['ts'])-time(visits[0]['ts'])).total_seconds()<point['min_dwell_s']:problems.append('insufficient_dwell')
            if valid:
                first=time(valid[0]['ts'])
                if prev_visit and first<prev_visit:problems.append('route_order')
                prev_visit=first
            for item in con.execute('SELECT * FROM item WHERE point_id=?',(point['id'],)):
                if not item['note'] and not any(p['sha256']==item['photo_hash'] for p in attached):problems.append('missing_item')
            if con.execute("SELECT 1 FROM problem WHERE point_id=? AND state!='closed'",(point['id'],)).fetchone():problems.append('unresolved_problem')
            if any(p['sha256'] in reused for p in attached):problems.append('reused_photo')
            problems=sorted(set(problems))
            for code in problems:con.execute('INSERT INTO issue(code,detail) VALUES (?,?)',(code,point['id']))
            result='待复核' if problems else '覆盖'
            evidence=json.dumps({'photos':[p['id'] for p in attached],'issues':problems},ensure_ascii=False)
            con.execute('INSERT INTO decision VALUES (?,?,?,?,?)',(point['id'],result,json.dumps(coordinates),evidence,'1'));results[point['id']]=result
    con.close();return results
def advance_problem(db_path,problem_id,new_state,evidence):
    allowed={'found':'assigned','assigned':'rechecked','rechecked':'closed'}
    con=connect(db_path)
    with con:
        old=con.execute('SELECT state FROM problem WHERE id=?',(problem_id,)).fetchone()
        if not old or allowed.get(old['state'])!=new_state or not evidence:raise ValueError('illegal_problem_transition')
        con.execute('UPDATE problem SET state=?,evidence=? WHERE id=?',(new_state,evidence,problem_id))
    con.close()
