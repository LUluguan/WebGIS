# -*- coding: utf-8 -*-
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app

def test_realevent_registry():
    c = TestClient(app.app)
    r = c.get("/api/realevent")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d.get("default"), "应有默认事件"
    ids = [e["id"] for e in d.get("events", [])]
    assert "yingde" in ids, "应包含英德事件: %s" % ids
    print("api realevent 注册表 OK:", d["default"], ids)

def test_realevent_meta():
    c = TestClient(app.app)
    r = c.get("/api/realevent/yingde")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["water_level_m"] > 0, "W 应为正"
    assert d["flooded_area_km2"] > 0, "淹没面积应为正"
    assert d["flood_bbox"], "应有淹没包络"
    assert d.get("assets"), "应有资产清单"
    print("api realevent/yingde OK:", d["water_level_m"], d["flooded_area_km2"], d["reliability"])

def test_realevent_unknown_404():
    c = TestClient(app.app)
    r = c.get("/api/realevent/__no_such__")
    assert r.status_code == 404, r.text[:200]
    print("api realevent 未知事件 404 OK")

def test_realevent_extent():
    c = TestClient(app.app)
    r = c.get("/api/realevent_extent", params={"event": "yingde"})
    assert r.status_code == 200, r.text[:200]
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) >= 1, "应有淹没多边形"
    meta = c.get("/api/realevent/yingde").json()
    w, s, e, n = meta["bbox"]
    for f in fc["features"]:
        for pt in f["geometry"]["coordinates"][0]:
            assert w - 0.01 <= pt[0] <= e + 0.01, pt
            assert s - 0.01 <= pt[1] <= n + 0.01, pt
    print("api realevent_extent OK:", len(fc["features"]), "个淹没多边形")

if __name__ == "__main__":
    test_realevent_registry()
    test_realevent_meta()
    test_realevent_unknown_404()
    test_realevent_extent()
    print("test_api_realevent OK")
