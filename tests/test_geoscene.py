# -*- coding: utf-8 -*-
import os, sys
from unittest.mock import patch
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app

def test_geoscene_disabled_by_default():
    c = TestClient(app.app)
    r = c.get("/api/geoscene")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert "enabled" in d and "extent_url" in d and "depth_url" in d
    # 真断言: 未配置 .env/环境变量时必须 enabled=False 且 enabled 由 URL 推导
    expected = bool(app.GEOSCENE_EXTENT_URL or app.GEOSCENE_DEPTH_URL)
    assert d["enabled"] == expected, (d["enabled"], expected)
    assert d["enabled"] == (bool(d["extent_url"]) or bool(d["depth_url"])), d
    print("geoscene enabled=%s(与配置一致)" % d["enabled"])

def test_geoscene_enabled_when_configured():
    with patch.dict(os.environ, {
            "GEOSCENE_EXTENT_URL": "https://services1.arcgis.com/org/arcgis/rest/services/extent/FeatureServer/0",
            "GEOSCENE_DEPTH_URL": "https://services1.arcgis.com/org/arcgis/rest/services/depth/ImageServer"}):
        import importlib
        importlib.reload(app)
        c = TestClient(app.app)
        d = c.get("/api/geoscene").json()
        assert d["enabled"] is True
        assert "arcgis.com" in d["extent_url"]
        print("geoscene enabled(配置后)=True OK")

def test_geoscene_enabled_with_just_extent():
    # 只发布淹没范围要素服务也应 enabled=true(形成 GeoScene 依赖)
    with patch.dict(os.environ, {
            "GEOSCENE_EXTENT_URL": "https://services1.arcgis.com/org/arcgis/rest/services/extent/FeatureServer/0"}):
        import importlib
        importlib.reload(app)
        c = TestClient(app.app)
        d = c.get("/api/geoscene").json()
        assert d["enabled"] is True
        assert d["depth_url"] == ""
        print("geoscene 仅配置 extent 也 enabled=True OK")

if __name__ == "__main__":
    test_geoscene_disabled_by_default()
    test_geoscene_enabled_when_configured()
    test_geoscene_enabled_with_just_extent()
    print("test_geoscene OK")
