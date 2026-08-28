# -*- coding: utf-8 -*-
"""安全白名单 + 新接口(影响/易涝点/在线模拟C)测试。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app

def test_static_whitelist():
    c = TestClient(app.app)
    # 允许: 前端资源
    assert c.get("/index.html").status_code == 200
    assert c.get("/welcome.html").status_code == 200
    assert c.get("/web/cesium/Cesium.js").status_code == 200
    assert c.get("/realevent_data.js").status_code == 200
    assert c.get("/realevent_out/realevent.json").status_code in (200, 404)  # 单事件布局可有可无
    # 拒绝: 点文件/敏感与大文件
    for p in ["/.env", "/.gitignore", "/app.py", "/requirements.txt", "/run.bat",
              "/unet_out/unet_water.pt", "/unet_out/unet_water_region.pt",
              "/realevent_out/yingde/_cache.npz", "/realevent_out/_cache.npz",
              "/C2132_作品介绍视频.mp4", "/README.md"]:
        r = c.get(p)
        assert r.status_code == 404, "%s 应被静态白名单拒绝, 实际 %s" % (p, r.status_code)
    print("静态白名单 OK: .env/.pt/.npz/.py/.bat/.mp4/.md 全部 404, 前端资源 200")

def test_impact():
    c = TestClient(app.app)
    r = c.get("/api/impact", params={"return_period": 100})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["buildings_total"] > 200, d
    assert d["affected_buildings"] <= d["buildings_total"]
    assert d["affected_population"] > 0, "受影响人口应为正"
    assert d["pop_source"] in ("worldpop", "estimate")
    assert d["flooded_land_km2"] > 0
    print("api impact OK: 建筑 %d/%d, 人口 %s(%s)" %
          (d["affected_buildings"], d["buildings_total"], d["affected_population"], d["pop_source"]))

def test_impact_monotonic():
    c = TestClient(app.app)
    low = c.get("/api/impact", params={"return_period": 2}).json()
    high = c.get("/api/impact", params={"return_period": 100}).json()
    assert high["affected_buildings"] >= low["affected_buildings"], (low, high)
    print("api impact 随重现期单调不减 OK (%d -> %d 栋)" % (low["affected_buildings"], high["affected_buildings"]))

def test_hotspots():
    c = TestClient(app.app)
    r = c.get("/api/hotspots", params={"return_period": 100, "top": 8})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    hs = d["hotspots"]
    assert len(hs) >= 3, "应有不少于3个淹没斑块"
    areas = [h["area_km2"] for h in hs]
    assert areas == sorted(areas, reverse=True), "应按面积降序"
    for h in hs:
        assert h["max_depth_m"] > 0 and h["bbox"] and len(h["bbox"]) == 4
        assert 113.30 - 0.01 <= h["bbox"][0] and h["bbox"][2] <= 113.34 + 0.01
        assert 23.09 - 0.01 <= h["bbox"][1] and h["bbox"][3] <= 23.13 + 0.01
    print("api hotspots OK: top%d, 最大斑块 %.3f km²" % (len(hs), hs[0]["area_km2"]))

def test_online_sim_runoff_coef():
    c = TestClient(app.app)
    d05 = c.get("/api/online_sim", params={"rain_mm": 300, "c": 0.50}).json()
    d035 = c.get("/api/online_sim", params={"rain_mm": 300, "c": 0.35}).json()
    assert d05["c"] == 0.50 and d035["c"] == 0.35
    assert d035["flooded_area_km2"] < d05["flooded_area_km2"], "海绵化(C降低)应减小淹没面积"
    assert d035["water_level_m"] < d05["water_level_m"], "C 降低应降低水位"
    assert "impact" in d05 and "hotspots" in d05, "在线模拟应返回影响统计与易涝点"
    assert d05["impact"]["buildings_total"] > 200
    print("online_sim 径流系数 C OK: 300mm 淹没 %.3f(C=0.50) -> %.3f km²(C=0.35)" %
          (d05["flooded_area_km2"], d035["flooded_area_km2"]))

def test_index_html_new_features():
    html = open("index.html", encoding="utf-8").read()
    for kw in ["cSlider", "stormBtn", "hsList", "evBtns", "baseBtn", "playStorm",
               "fetchImpact", "/api/hotspots", "/api/impact", "analysis.html",
               "switchEvent", "/api/realevent", "animateWaterTo"]:
        assert kw in html, "index.html 缺新功能: %s" % kw
    print("index.html 新功能标记齐备 OK")

def test_analysis_html():
    html = open("analysis.html", encoding="utf-8").read()
    assert "js.geoscene.cn" in html, "应引用 GeoScene JS API"
    assert "geoscene/Map" in html and "FeatureLayer" in html, "应使用 GeoScene API 模块"
    assert "return_yr" in html and "definitionExpression" in html, "应按 return_yr 过滤要素服务"
    print("analysis.html GeoScene API 检查 OK")

def test_welcome_links():
    html = open("welcome.html", encoding="utf-8").read()
    for href in ["index.html", "dashboard.html", "realevent.html", "analysis.html"]:
        assert href in html, "欢迎页缺入口 %s" % href
    assert "演示流程" in html
    print("welcome 入口与演示脚本 OK")

if __name__ == "__main__":
    test_static_whitelist()
    test_impact()
    test_impact_monotonic()
    test_hotspots()
    test_online_sim_runoff_coef()
    test_index_html_new_features()
    test_analysis_html()
    test_welcome_links()
    print("test_security_features OK")
