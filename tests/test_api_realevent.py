# -*- coding: utf-8 -*-
import os, sys, json, io
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_realevent():
    c = TestClient(app.app)
    r = c.get("/api/realevent")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["water_level_m"] > 0, "W 应为正"
    assert d["flooded_area_km2"] > 0, "淹没面积应为正"
    assert d["flood_bbox"], "应有淹没包络"
    print("api realevent OK:", d["water_level_m"], d["flooded_area_km2"], d["reliability"])

def test_realevent_extent():
    c = TestClient(app.app)
    r = c.get("/api/realevent_extent")
    assert r.status_code == 200, r.text[:200]
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) >= 1, "应有淹没多边形"
    # 多边形应在英德 bbox 内
    d = c.get("/api/realevent").json()
    w, s, e, n = d["bbox"]
    for f in fc["features"]:
        for pt in f["geometry"]["coordinates"][0]:
            assert w - 0.01 <= pt[0] <= e + 0.01, pt
            assert s - 0.01 <= pt[1] <= n + 0.01, pt
    print("api realevent_extent OK:", len(fc["features"]), "个淹没多边形")

if __name__ == "__main__":
    test_realevent()
    test_realevent_extent()
    print("test_api_realevent OK")
