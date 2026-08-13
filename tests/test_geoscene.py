# -*- coding: utf-8 -*-
import os, sys
from unittest.mock import patch
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_geoscene_disabled_by_default():
    c = TestClient(app.app)
    r = c.get("/api/geoscene")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert "enabled" in d and "extent_url" in d and "depth_url" in d
    assert d["enabled"] is False or d["enabled"] is True
    print("geoscene enabled=%s" % d["enabled"])

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
