"""Offline captures of supplier catalog pages."""

import json
from pathlib import Path

from .ingest import source, time
from .store import connect


def _schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS supplier_offer(
      capture_id TEXT NOT NULL, offer_id TEXT NOT NULL, supplier_id TEXT NOT NULL,
      item_id TEXT NOT NULL, pack_size INTEGER NOT NULL, stock_packs INTEGER NOT NULL,
      price_cents INTEGER NOT NULL, delivery_at TEXT NOT NULL, observed_at TEXT NOT NULL,
      source_id INTEGER NOT NULL REFERENCES source(id),
      PRIMARY KEY(capture_id,offer_id));
    """)


def import_page(path, db_path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    db = connect(db_path)
    try:
        _schema(db)
        with db:
            sid, duplicate = source(db, path)
            if duplicate:
                return {"imported": 0, "duplicate_file": True}
            time(doc["observed_at"])
            for row in doc.get("offers", []):
                time(row["delivery_at"])
                if min(int(row["pack_size"]), int(row["stock_packs"]) + 1,
                       int(row["price_cents"])) <= 0:
                    raise ValueError("invalid_offer")
                db.execute("INSERT INTO supplier_offer VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (doc["capture_id"], row["offer_id"], row["supplier_id"],
                            row["item_id"], int(row["pack_size"]), int(row["stock_packs"]),
                            int(row["price_cents"]), row["delivery_at"],
                            doc["observed_at"], sid))
        return {"imported": len(doc.get("offers", [])), "duplicate_file": False}
    finally:
        db.close()


def latest_offers(db, item_id):
    _schema(db)
    rows = db.execute("""SELECT * FROM supplier_offer WHERE item_id=?
      ORDER BY observed_at DESC,capture_id DESC""", (item_id,)).fetchall()
    latest = {}
    for row in rows:
        latest.setdefault(row["offer_id"], dict(row))
    return list(latest.values())
