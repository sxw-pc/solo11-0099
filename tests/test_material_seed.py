import json

from sitewalk.__main__ import init
from sitewalk.install_gate import assess
from sitewalk.material import import_file


def test_material_baseline(tmp_path):
    db = tmp_path / "sitewalk.db"
    init(db)
    file = tmp_path / "material.json"
    file.write_text(json.dumps({"lots": [{"id": "L1", "status": "released"}],
                                "scans": [{"event_id": "E1", "lot_id": "L1",
                                           "piece_id": "C1", "point_id": "A",
                                           "kind": "install", "at": "2026-01-01T00:06:00Z"}]}),
                    encoding="utf-8")
    assert import_file(file, db)["imported"] == 1
    assert import_file(file, db)["duplicate_file"]
    assert assess(db, "A") == {"point_id": "A", "status": "ready",
                               "reason": "latest_scans_installed", "pieces": ["C1"]}
