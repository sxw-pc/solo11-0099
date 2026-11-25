import argparse
import base64
import json
import tempfile
from pathlib import Path
from .ingest import photos,tracks,items
from .rules import evaluate
from .store import connect
from .plan import load_plan
from .rules import advance_problem
def init(db_path):
    con=connect(db_path)
    with con:
        con.execute('INSERT OR IGNORE INTO plan VALUES (?,?,?,?)',('p1',1,'2026-01-01T00:00:00Z','2027-01-01T00:00:00Z'))
        con.execute('INSERT OR IGNORE INTO point VALUES (?,?,?,?,?,?,?)',('A','p1',30,120,50,0,1))
        con.execute('INSERT OR IGNORE INTO batch VALUES (?,?,?)',('demo','p1','ready'))
    con.close()
def report(db_path,path):
    con=connect(db_path);data={name:[dict(r) for r in con.execute('SELECT * FROM '+name)] for name in ('plan','batch','point','source','asset','photo','track','item','problem','run','evidence','issue','decision')}
    for row in data['issue']:row.pop('id',None)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=lambda v:{'base64':base64.b64encode(v).decode('ascii')}),encoding='utf-8');path.with_suffix('.md').write_text('# 巡检覆盖结论\n\n'+'\n'.join(f"- {d['point_id']}: {d['result']}（规则 {d['rules_version']}）" for d in data['decision'])+'\n',encoding='utf-8');con.close();return path
def demo(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);db=folder/'sitewalk.db';init(db)
    (folder/'photo.txt').write_text('synthetic photo bytes',encoding='utf-8')
    (folder/'photos.jsonl').write_text(json.dumps({'point_id':'A','ts':'2026-01-01T00:05:00Z','file':'photo.txt'}),encoding='utf-8')
    (folder/'track.csv').write_text('ts,lat,lon\n2026-01-01T00:00:00Z,30,120\n2026-01-01T00:10:00Z,30,120\n',encoding='utf-8')
    photos(folder/'photos.jsonl',db);tracks(folder/'track.csv',db);evaluate(db);return report(db,folder/'report.json')
def main():
    p=argparse.ArgumentParser(description='离线工地巡检证据台');s=p.add_subparsers(dest='cmd',required=True)
    d=s.add_parser('demo');d.add_argument('--workdir')
    i=s.add_parser('init');i.add_argument('--db',required=True);i.add_argument('--plan')
    a=s.add_parser('ingest');a.add_argument('--db',required=True);a.add_argument('--photos');a.add_argument('--track');a.add_argument('--items')
    e=s.add_parser('evaluate');e.add_argument('--db',required=True)
    r=s.add_parser('report');r.add_argument('--db',required=True);r.add_argument('--out',required=True)
    x=s.add_parser('inspect');x.add_argument('--db',required=True);x.add_argument('--point',required=True)
    m=s.add_parser('problem');m.add_argument('--db',required=True);m.add_argument('--id',required=True);m.add_argument('--state',required=True);m.add_argument('--evidence',required=True);m.add_argument('--point')
    args=p.parse_args()
    if args.cmd=='demo':print(demo(args.workdir or tempfile.mkdtemp(prefix='sitewalk-')))
    elif args.cmd=='init':load_plan(args.plan,args.db) if args.plan else init(args.db)
    elif args.cmd=='ingest':print({k:fn(getattr(args,k),args.db) for k,fn in [('photos',photos),('track',tracks),('items',items)] if getattr(args,k)})
    elif args.cmd=='evaluate':print(evaluate(args.db))
    elif args.cmd=='report':print(report(args.db,args.out))
    elif args.cmd=='problem':
        if args.state=='found':
            con=connect(args.db)
            with con:con.execute('INSERT INTO problem VALUES (?,?,?,?)',(args.id,args.point,'found',args.evidence))
            con.close()
        else:advance_problem(args.db,args.id,args.state,args.evidence)
    else:
        con=connect(args.db);print(json.dumps({'decision':[dict(r) for r in con.execute('SELECT * FROM decision WHERE point_id=?',(args.point,))],'photos':[dict(r) for r in con.execute('SELECT * FROM photo WHERE point_id=?',(args.point,))]},ensure_ascii=False));con.close()
if __name__=='__main__':main()
