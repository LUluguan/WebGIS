# -*- coding: utf-8 -*-
import os, sys
from unittest.mock import patch
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app

def test_monthly_rain_ok():
    c = TestClient(app.app)
    r = c.get("/api/monthly_rain")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    # precip_tif 可能缺失(未提交)或存在; 两种都应 200 且结构合法
    assert "years" in d and "months" in d and "monthly_rain" in d
    print("monthly_rain status=200, years=%d" % len(d["years"]))

def test_monthly_rain_fallback_when_missing():
    real_exists = os.path.exists

    def fake_exists(p):
        return False if "precip_tif" in str(p) else real_exists(p)

    c = TestClient(app.app)
    with patch("app.os.path.exists", side_effect=fake_exists):
        r = c.get("/api/monthly_rain")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["years"] == [] and d["monthly_rain"] == {}, d
    print("monthly_rain 缺失回退 OK")

def test_depth_hist_period_variant():
    c = TestClient(app.app)
    h2 = c.get("/api/depth_hist", params={"return_period": 2}).json()
    h100 = c.get("/api/depth_hist", params={"return_period": 100}).json()
    assert h2["return_period"] == 2 and h100["return_period"] == 100
    assert len(h2["counts"]) == 6 and len(h100["counts"]) == 6
    assert h2["counts"] != h100["counts"], "不同重现期水深分布应不同"
    assert sum(h2["counts"]) < sum(h100["counts"]), "高重现期淹没格网应更多"
    assert h2["warn"] != h100["warn"]
    # 排除河道后: 陆地水深 ≤ 水位W(<5m), >5m 档应为 0, 且 3-5m 档不再被河道主导
    assert h100["counts"][5] == 0, "排除河道后不应有>5m陆地水深"
    assert h2["counts"][5] == 0
    assert h100["counts"][4] < 500, "3-5m档不应再包含河道(应只数百格陆地)"
    print("depth_hist 随重现期动态变化 + 排除河道 OK")

if __name__ == "__main__":
    test_monthly_rain_ok()
    test_monthly_rain_fallback_when_missing()
    test_depth_hist_period_variant()
    print("test_api_fallback OK")
