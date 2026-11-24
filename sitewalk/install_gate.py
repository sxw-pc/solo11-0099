"""Provisional installed-material release decision."""

from .material import provisional_at
from .store import connect


def assess(db_path, point_id):
    db = connect(db_path)
    try:
        if not db.execute("SELECT 1 FROM point WHERE id=?", (point_id,)).fetchone():
            raise ValueError("unknown_point")
        placed = provisional_at(db, point_id)
        if not placed:
            status, reason = "review", "no_installed_piece"
        elif any(row["lot_status"] != "released" for row in placed):
            status, reason = "review", "lot_on_hold"
        else:
            status, reason = "ready", "latest_scans_installed"
        return {"point_id": point_id, "status": status, "reason": reason,
                "pieces": sorted(row["piece_id"] for row in placed)}
    finally:
        db.close()
