# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_online_sim_consistency_scenarios():
    """在线模拟在 P-III 设计雨量处应与预置重现期场景口径一致(W/面积/分区)。"""
    c = TestClient(app.app)
    scens = {s["return_period_y"]: s for s in c.get("/api/scenarios").json()}
    for T, s in scens.items():
        d = c.get("/api/online_sim", params={"rain_mm": s["rain_mm"]}).json()
        assert abs(d["water_level_m"] - s["water_level_m"]) < 0.06, (T, d["water_level_m"], s["water_level_m"])
        assert abs(d["flooded_area_km2"] - s["flooded_area_km2"]) < 0.05, (T, d["flooded_area_km2"], s["flooded_area_km2"])
        z = c.get("/api/zone_flood", params={"return_period": T, "grid": 3}).json()["zones"]
        for k in range(6):
            assert abs(d["zones"][k] - z[k]) < 1.0, (T, k, d["zones"][k], z[k])
    print("online_sim 与 5 档重现期口径一致 OK")

def test_online_sim_monotonic():
    c = TestClient(app.app)
    prev = (0.0, 0.0, 0)
    for r in (50, 100, 150, 200, 300.6, 500):
        d = c.get("/api/online_sim", params={"rain_mm": r}).json()
        assert d["water_level_m"] >= prev[0] - 1e-6, (r, d["water_level_m"], prev[0])
        assert d["flooded_area_km2"] >= prev[1] - 1e-6, (r, d["flooded_area_km2"], prev[1])
        assert d["flooded_cells"] >= prev[2], (r, d["flooded_cells"], prev[2])
        prev = (d["water_level_m"], d["flooded_area_km2"], d["flooded_cells"])
    print("online_sim 随雨量单调不减 OK (500mm -> W=%.2fm)" % prev[0])

def test_online_sim_extent_in_bbox():
    c = TestClient(app.app)
    d = c.get("/api/online_sim", params={"rain_mm": 250}).json()
    assert d["extent"]["type"] == "FeatureCollection" and len(d["extent"]["features"]) >= 1
    for f in d["extent"]["features"]:
        for pt in f["geometry"]["coordinates"][0]:
            assert 113.30 - 0.01 <= pt[0] <= 113.34 + 0.01, pt
            assert 23.09 - 0.01 <= pt[1] <= 23.13 + 0.01, pt
    print("online_sim extent 在研究区 bbox 内 OK (%d 斑块)" % len(d["extent"]["features"]))

def test_online_sim_small_rain():
    c = TestClient(app.app)
    d = c.get("/api/online_sim", params={"rain_mm": 10}).json()
    assert d["flooded_cells"] < 907, d      # 2年(118.5mm)为 907 格, 10mm 应更少
    assert d["water_level_m"] < 3.11, d
    print("online_sim 小雨量淹没范围小于 2 年档 OK")

def test_online_sim_invalid():
    c = TestClient(app.app)
    assert c.get("/api/online_sim").status_code == 422
    assert c.get("/api/online_sim", params={"rain_mm": -5}).status_code == 422
    assert c.get("/api/online_sim", params={"rain_mm": 3000}).status_code == 422
    print("online_sim 参数校验(缺/负/超2000) 422 OK")

if __name__ == "__main__":
    test_online_sim_consistency_scenarios()
    test_online_sim_monotonic()
    test_online_sim_extent_in_bbox()
    test_online_sim_small_rain()
    test_online_sim_invalid()
    print("test_online_sim OK")
