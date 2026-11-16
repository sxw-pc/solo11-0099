import json
from .ingest import time
from .store import connect

def load_plan(path,db_path):
    data=json.loads(open(path,encoding='utf-8').read())
    plan=data['plan'];points=data['points']
    if time(plan['valid_from'])>=time(plan['valid_to']):raise ValueError('invalid_plan_window')
    if len({p['id'] for p in points})!=len(points):raise ValueError('duplicate_point')
    if len({p['order'] for p in points})!=len(points):raise ValueError('duplicate_order')
    con=connect(db_path)
    with con:
        old=con.execute('SELECT * FROM plan WHERE id=?',(plan['id'],)).fetchone()
        if old:raise ValueError('plan_immutable')
        con.execute('INSERT INTO plan VALUES (?,?,?,?)',(plan['id'],int(plan['version']),plan['valid_from'],plan['valid_to']))
        con.execute('INSERT INTO batch VALUES (?,?,?)',(data.get('batch_id',plan['id']+'-batch'),plan['id'],'ready'))
        for point in points:
            if float(point['radius_m'])<=0 or int(point['min_dwell_s'])<0:raise ValueError('invalid_point_rule')
            con.execute('INSERT INTO point VALUES (?,?,?,?,?,?,?)',(point['id'],plan['id'],float(point['lat']),float(point['lon']),float(point['radius_m']),int(point['min_dwell_s']),int(point['order'])))
    con.close();return len(points)
