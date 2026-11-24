"""Provisional procurement decision for a planned site visit."""

from .catalog import latest_offers
from .ingest import time
from .store import connect


def assess(db_path, point_id, item_id, quantity, deadline):
    if int(quantity) <= 0:
        raise ValueError("invalid_quantity")
    cutoff = time(deadline)
    db = connect(db_path)
    try:
        if not db.execute("SELECT 1 FROM point WHERE id=?", (point_id,)).fetchone():
            raise ValueError("unknown_point")
        eligible = [row for row in latest_offers(db, item_id)
                    if row["pack_size"] * row["stock_packs"] >= quantity
                    and time(row["delivery_at"]) <= cutoff]
        if not eligible:
            return {"point_id": point_id, "status": "review", "reason": "no_offer"}
        chosen = min(eligible, key=lambda row: (row["price_cents"], row["offer_id"]))
        return {"point_id": point_id, "status": "ready", "reason": "offer_available",
                "offer_id": chosen["offer_id"], "supplier_id": chosen["supplier_id"]}
    finally:
        db.close()
