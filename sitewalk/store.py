import sqlite3
from pathlib import Path
SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS plan(id TEXT PRIMARY KEY, version INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS point(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id), lat REAL NOT NULL, lon REAL NOT NULL, radius_m REAL NOT NULL, min_dwell_s INTEGER NOT NULL, ord INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source(id INTEGER PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS asset(sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS photo(id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id), point_id TEXT, ts TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS track(id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id), ts TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
CREATE TABLE IF NOT EXISTS item(id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES point(id), photo_hash TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS problem(id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES point(id), state TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue(id INTEGER PRIMARY KEY, code TEXT NOT NULL, detail TEXT NOT NULL, source_id INTEGER REFERENCES source(id));
CREATE TABLE IF NOT EXISTS decision(point_id TEXT PRIMARY KEY REFERENCES point(id), result TEXT NOT NULL, position TEXT, evidence TEXT NOT NULL, rules_version TEXT NOT NULL);
"""
def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path);con.row_factory=sqlite3.Row;con.executescript(SCHEMA);return con
