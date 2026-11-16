import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS plan(
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    route_contract TEXT NOT NULL DEFAULT 'fixed_order'
        CHECK(route_contract IN ('fixed_order','dependency')),
    digest TEXT
);
CREATE TABLE IF NOT EXISTS batch(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id), state TEXT NOT NULL CHECK(state IN ('ready','review','decided')));
CREATE TABLE IF NOT EXISTS point(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plan(id), lat REAL NOT NULL, lon REAL NOT NULL, radius_m REAL NOT NULL, min_dwell_s INTEGER NOT NULL, ord INTEGER NOT NULL);
-- A prerequisite row means "before" must be checked before "after".
CREATE TABLE IF NOT EXISTS prerequisite(
    plan_id TEXT NOT NULL REFERENCES plan(id),
    before TEXT NOT NULL,
    after TEXT NOT NULL,
    PRIMARY KEY(plan_id, before, after),
    CHECK(before != after)
);
CREATE TABLE IF NOT EXISTS source(id INTEGER PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, raw BLOB NOT NULL DEFAULT X'', batch_id TEXT REFERENCES batch(id));
CREATE TABLE IF NOT EXISTS asset(sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL, raw BLOB NOT NULL DEFAULT X'');
CREATE TABLE IF NOT EXISTS photo(
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    point_id TEXT,
    ts TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    payload TEXT NOT NULL,
    plan_version INTEGER
);
CREATE TABLE IF NOT EXISTS track(id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES source(id), ts TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
CREATE TABLE IF NOT EXISTS item(id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES point(id), photo_hash TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS problem(id TEXT PRIMARY KEY, point_id TEXT NOT NULL REFERENCES point(id), state TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue(id INTEGER PRIMARY KEY, code TEXT NOT NULL, detail TEXT NOT NULL, source_id INTEGER REFERENCES source(id));
CREATE TABLE IF NOT EXISTS run(id TEXT PRIMARY KEY, rules_version TEXT NOT NULL, source_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), subject_id TEXT NOT NULL, source_id INTEGER REFERENCES source(id), object_kind TEXT NOT NULL, object_id INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision(
    point_id TEXT PRIMARY KEY REFERENCES point(id),
    result TEXT NOT NULL,
    position TEXT,
    evidence TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(id),
    plan_version INTEGER,
    route_contract TEXT,
    arrival_order TEXT,
    violated_edges TEXT,
    track_interval TEXT,
    evidence_hash TEXT
);
-- One signed-off route verdict per plan/run/contract.  It records the actual
-- arrival order, violated prerequisite edges, uncovered points and the hash of
-- the per-point evidence behind the verdict.
CREATE TABLE IF NOT EXISTS route_check(
    plan_id TEXT NOT NULL REFERENCES plan(id),
    run_id TEXT NOT NULL REFERENCES run(id),
    contract TEXT NOT NULL CHECK(contract IN ('fixed_order','dependency')),
    arrival_order TEXT NOT NULL,
    violated_edges TEXT NOT NULL,
    missing_points TEXT NOT NULL,
    result TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    evidence_hash TEXT NOT NULL,
    PRIMARY KEY(plan_id, run_id, contract)
);
"""

# Columns added after the first schema version.  CREATE TABLE IF NOT EXISTS
# cannot add columns to an existing local database, so connect() migrates it in
# place while keeping historical evidence.
_MIGRATIONS = {
    'plan': {
        'route_contract': "TEXT NOT NULL DEFAULT 'fixed_order'",
        'digest': 'TEXT',
    },
    'photo': {
        'plan_version': 'INTEGER',
    },
    'decision': {
        'plan_version': 'INTEGER',
        'route_contract': 'TEXT',
        'arrival_order': 'TEXT',
        'violated_edges': 'TEXT',
        'track_interval': 'TEXT',
        'evidence_hash': 'TEXT',
    },
}


def _migrate(con):
    for table, columns in _MIGRATIONS.items():
        present = {row['name'] for row in con.execute(f'PRAGMA table_info({table})')}
        for name, declaration in columns.items():
            if name not in present:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    return con
