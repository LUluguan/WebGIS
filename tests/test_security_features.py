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
              "/C2132_作品介绍视频.mp4", "/README.md",
              # 凭据/隐私 JSON basename 黑名单(2026-10 安全审计)
              "/web_users.json", "/reports/subscribers.json", "/reports/reports.json"]:
        r = c.get(p)
        assert r.status_code == 404, "%s 应被静态白名单拒绝, 实际 %s" % (p, r.status_code)
    # 报汛照片仍需可访问(移动端列表展示)
    assert c.get("/reports/").status_code in (200, 404)
    print("静态白名单 OK: .env/.pt/.npz/.py/.bat/.mp4/.md/凭据JSON 全部 404, 前端资源 200")

def test_impact():
    c = TestClient(app.app)
    r = c.get("/api/impact", params={"return_period": 100})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["buildings_total"] > 200, d
    assert d["affected_buildings"] <= d["buildings_total"]
    assert d["affected_population"] > 0, "受影响人口应为正"
    # 口径与文件状态强绑定: 有格网必须走 worldpop, 没格网必须走 estimate(不允许静默错配)
    expect_src = "worldpop" if os.path.exists(os.path.join(ROOT, "dem", "study_pop.tif")) else "estimate"
    assert d["pop_source"] == expect_src, (d["pop_source"], expect_src)
    assert d["flooded_land_km2"] > 0
    print("api impact OK: 建筑 %d/%d, 人口 %s(%s)" %
          (d["affected_buildings"], d["buildings_total"], d["affected_population"], d["pop_source"]))


def test_flood_extent_invalid_returns_404():
    """非法重现期(非 2/5/10/50/100 档)应 404 而非 500(2026-10 审计: 裸 open 抛 FileNotFoundError)。"""
    c = TestClient(app.app)
    r = c.get("/api/flood_extent", params={"return_period": 3})
    assert r.status_code == 404, r.status_code
    assert "2/5/10/50/100" in r.json()["error"]
    print("flood_extent 非法重现期 404 OK")


def test_report_xss_and_abuse_hardening():
    """存储型 XSS + 滥用加固(2026-10 审计实证链路):
    匿名 POST 入库 → 列表 innerHTML 渲染。后端剥标签 + 类型 400 + IP 频控; 双斜杠探测 404。
    自清理: 结束后恢复 reports.json 快照, 不给交付数据留测试残留。"""
    c = TestClient(app.app)
    import json as _json
    rp = os.path.join(ROOT, "reports", "reports.json")
    snap = _json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else []
    try:
        # XSS 载荷入库前必须剥标签
        r = c.post("/api/report", json={
            "location_text": "XSS<b>probe</b><img src=x onerror=alert(1)>",
            "desc": "<script>alert(1)</script>积水"})
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert "<b>" not in item["location_text"] and "<img" not in item["location_text"], item
        assert "<script>" not in item["desc"], item["desc"]
        # 类型错乱 → 400 且不回显内部异常文本
        r = c.post("/api/report", json={"lon": "abc"})
        assert r.status_code == 400 and "could not convert" not in r.text, r.text
        # 频控: 临时收紧到 5 条/600s 断言 429(生产默认已放宽为 20 条/60 秒, 现场连点不撞)
        orig_rate = app._rate_ok
        app._rate_ok = lambda ip, limit=5, window=600.0: orig_rate(ip, limit=5, window=600.0)
        try:
            codes = [c.post("/api/report", json={"location_text": "rate"}).status_code for _ in range(7)]
        finally:
            app._rate_ok = orig_rate
        assert 429 in codes, codes
    finally:
        _json.dump(snap, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for f in os.listdir(os.path.join(ROOT, "reports")):
            if f.startswith("img_r9") and _json.dumps(snap).find(f.rsplit(".", 1)[0]) < 0:
                try:
                    os.remove(os.path.join(ROOT, "reports", f))
                except OSError:
                    pass
    # 双斜杠探测("//web_users.json" 等)在真实服务器上已 404(redirect_slashes=False + basename 黑名单,
    # curl 实测 5 条全 404)。无法用 TestClient 断言: httpx 会把 // 路径折叠改写, 探测不到真实路由。
    print("report XSS 消毒/类型 400/频控 429 OK(双斜杠 404 由 curl 实测验证; 数据已自清理)")


def test_report_concurrent_no_loss():
    """2026-10 审计 P1 实证: 无锁时 3 发成功只落盘 2 条且 id 撞车。
    修复后 _report_lock 全程串行 → 10 并发不丢、不重。"""
    import threading
    c = TestClient(app.app)
    orig = app._rate_ok
    app._rate_ok = lambda ip, limit=20, window=60.0: True   # 绕开频控专测并发
    ids, errs = [], []
    lk = threading.Lock()

    def worker(i):
        cl = TestClient(app.app)
        try:
            r = cl.post("/api/report", json={"location_text": "并发测试%d" % i})
            rid = (r.json() or {}).get("id")
            with lk:
                ids.append(rid)
        except Exception as e:
            with lk:
                errs.append(str(e))

    try:
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        app._rate_ok = orig
    assert not errs, errs
    assert len(ids) == 10 and len(set(ids)) == 10, "id 丢失或撞车: %d/%d" % (len(ids), len(set(ids)))
    import json as _json
    lst = _json.load(open(os.path.join(ROOT, "reports", "reports.json"), encoding="utf-8"))
    on_disk = set(x["id"] for x in lst)
    assert set(ids) <= on_disk, "有并发返回的 id 未落盘"
    # 清理并发测试数据
    app._save_reports([x for x in lst if not str(x.get("location_text", "")).startswith("并发测试")])
    print("并发上报 10/10 不丢、不重 OK")

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

def test_no_hardcoded_keys():
    """凭据防再犯: 被跟踪页面不得内嵌天地图 key 字面量(32位hex)或 Ion JWT;
    key 一律放 web/config.local.js(gitignored), 页面只留读取代码。"""
    import re, subprocess
    pat_tdt = re.compile(r"['\"][0-9a-f]{32}['\"]")
    pat_jwt = re.compile(r"eyJ[A-Za-z0-9_-]{20,}")
    for f in ["index.html", "flood.html", "realevent.html", "analysis.html", "dashboard.html", "unet.html"]:
        html = open(f, encoding="utf-8").read()
        assert not pat_tdt.search(html), "%s 仍内嵌 32 位 key 字面量" % f
        assert not pat_jwt.search(html), "%s 仍内嵌 Ion JWT" % f
        if f in ("index.html", "flood.html", "realevent.html"):
            assert "config.local.js" in html, "%s 未引用 web/config.local.js" % f
    # config.local.js 必须被 gitignore 且未被跟踪
    r = subprocess.run(["git", "check-ignore", "web/config.local.js"], capture_output=True)
    assert r.returncode == 0, "web/config.local.js 应被 .gitignore 排除"
    r = subprocess.run(["git", "ls-files", "web/config.local.js"], capture_output=True)
    assert not r.stdout.strip(), "web/config.local.js 不得被 git 跟踪"
    print("凭据外置检查 OK(无内嵌 key/JWT, config.local.js 未跟踪)")

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
    # 2026-09 用户要求移除「建议演示流程」板块, 断言其不再出现
    assert "演示流程" not in html
    print("welcome 入口 OK(演示流程板块已按要求移除)")

if __name__ == "__main__":
    test_static_whitelist()
    test_impact()
    test_flood_extent_invalid_returns_404()
    test_impact_monotonic()
    test_hotspots()
    test_online_sim_runoff_coef()
    test_index_html_new_features()
    test_analysis_html()
    test_welcome_links()
    test_no_hardcoded_keys()
    test_report_xss_and_abuse_hardening()
    test_report_concurrent_no_loss()
    print("test_security_features OK")
