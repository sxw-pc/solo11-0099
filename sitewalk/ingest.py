import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from .store import connect
def sha(data):return hashlib.sha256(data).hexdigest()
def time(s):
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if not t.tzinfo:raise ValueError('timezone_required')
    return t
def source(con,path):
    raw=Path(path).read_bytes();h=sha(raw)
    old=con.execute('SELECT id FROM source WHERE sha256=?',(h,)).fetchone()
    if old:return old['id'],True
    return con.execute('INSERT INTO source(path,sha256) VALUES (?,?)',(str(path),h)).lastrowid,False
def photos(path,db_path):
    con=connect(db_path);count=0
    with con:
        sid,done=source(con,path)
        if done:return 0
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            try:
                row=json.loads(line);point=row['point_id'];ts=row['ts'];time(ts)
                photo=Path(path).parent/row['file'];content_hash=sha(photo.read_bytes())
                if not con.execute('SELECT 1 FROM point WHERE id=?',(point,)).fetchone():raise ValueError('unknown_point')
                con.execute('INSERT INTO photo(source_id,point_id,ts,sha256,payload) VALUES (?,?,?,?,?)',(sid,point,ts,content_hash,line));count+=1
            except (KeyError,ValueError,OSError,json.JSONDecodeError) as exc:con.execute('INSERT INTO issue(code,detail,source_id) VALUES (?,?,?)',('photo_error',str(exc),sid))
    con.close();return count
def tracks(path,db_path):
    con=connect(db_path);count=0
    with con:
        sid,done=source(con,path)
        if done:return 0
        for row in csv.DictReader(Path(path).open(encoding='utf-8-sig',newline='')):
            try:
                time(row['ts']);lat=float(row['lat']);lon=float(row['lon'])
                if abs(lat)>90 or abs(lon)>180:raise ValueError('coordinate_range')
                con.execute('INSERT INTO track(source_id,ts,lat,lon) VALUES (?,?,?,?,)',(sid,row['ts'],lat,lon));count+=1
            except (KeyError,ValueError) as exc:con.execute('INSERT INTO issue(code,detail,source_id) VALUES (?,?,?)',('track_error',str(exc),sid))
    con.close();return count
def items(path,db_path):
    con=connect(db_path);count=0
    with con:
        sid,done=source(con,path)
        if done:return 0
        for row in json.loads(Path(path).read_text(encoding='utf-8')):
            try:
                con.execute('INSERT INTO item VALUES (?,?,?,?)',(row['id'],row['point_id'],row.get('photo_hash'),row.get('note')));count+=1
            except (KeyError,ValueError) as exc:con.execute('INSERT INTO issue(code,detail,source_id) VALUES (?,?,?)',('item_error',str(exc),sid))
    con.close();return count
