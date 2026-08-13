# -*- coding: utf-8 -*-
import os, sys
from unittest.mock import patch
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
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

if __name__ == "__main__":
    test_monthly_rain_ok()
    test_monthly_rain_fallback_when_missing()
    print("test_api_fallback OK")
