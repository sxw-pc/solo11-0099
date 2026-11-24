import json

from sitewalk.__main__ import init
from sitewalk.buy_gate import assess
from sitewalk.catalog import import_page


def test_catalog_baseline(tmp_path):
    db = tmp_path / "sitewalk.db"
    init(db)
    page = tmp_path / "page.json"
    page.write_text(json.dumps({"capture_id": "P1", "observed_at": "2026-01-01T00:00:00Z",
                                "offers": [{"offer_id": "O1", "supplier_id": "S1",
                                            "item_id": "M1", "pack_size": 5,
                                            "stock_packs": 2, "price_cents": 100,
                                            "delivery_at": "2026-01-02T00:00:00Z"}]}),
                    encoding="utf-8")
    assert import_page(page, db)["imported"] == 1
    assert import_page(page, db)["duplicate_file"]
    assert assess(db, "A", "M1", 10, "2026-01-03T00:00:00Z") == {
        "point_id": "A", "status": "ready", "reason": "offer_available",
        "offer_id": "O1", "supplier_id": "S1"}
