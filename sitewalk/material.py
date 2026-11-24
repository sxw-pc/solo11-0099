"""Offline material scans for the site installation ledger."""

import json
from pathlib import Path

from .ingest import source, time
from .store import connect


def _schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS material_lot(id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS material_scan(
      event_id TEXT PRIMARY KEY, lot_id TEXT NOT NULL REFERENCES material_lot(id),
      piece_id TEXT NOT NULL, point_id TEXT NOT NULL REFERENCES point(id),
      kind TEXT NOT NULL, at TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES source(id));
    """)


def import_file(path, db_path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    db = connect(db_path)
    try:
        _schema(db)
        with db:
            sid, duplicate = source(db, path)
            if duplicate:
                return {"imported": 0, "duplicate_file": True}
            for lot in doc.get("lots", []):
                if lot["status"] not in ("released", "hold"):
                    raise ValueError("invalid_lot_status")
                db.execute("INSERT INTO material_lot VALUES (?,?)", (lot["id"], lot["status"]))
            for row in doc.get("scans", []):
                if row["kind"] not in ("install", "return"):
                    raise ValueError("invalid_scan_kind")
                time(row["at"])
                db.execute("INSERT INTO material_scan VALUES (?,?,?,?,?,?,?)",
                           (row["event_id"], row["lot_id"], row["piece_id"],
                            row["point_id"], row["kind"], row["at"], sid))
        return {"imported": len(doc.get("scans", [])), "duplicate_file": False}
    finally:
        db.close()


def provisional_at(db, point_id):
    """Baseline: use each piece's latest timestamped scan as its location."""
    _schema(db)
    rows = db.execute("""SELECT s.*,l.status AS lot_status FROM material_scan s
      JOIN material_lot l ON l.id=s.lot_id ORDER BY s.at DESC,s.event_id DESC""").fetchall()
    latest = {}
    for row in rows:
        latest.setdefault(row["piece_id"], row)
    return [dict(row) for row in latest.values()
            if row["point_id"] == point_id and row["kind"] == "install"]
