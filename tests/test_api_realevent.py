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

if __name__ == "__main__":
    test_realevent()
    print("test_api_realevent OK")
