# -*- coding: utf-8 -*-
"""复赛增强功能测试: 认证/预警/损失/疏散/专题图/问答/实时雨情/公众报汛。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app


def _client():
    return TestClient(app.app)


def _admin_token(c):
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_auth_login_me_logout():
    c = _client()
    tok = _admin_token(c)
    r = c.get("/api/auth/me", params={"token": tok})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # 公众账号
    r = c.post("/api/auth/login", json={"username": "public", "password": "123456"})
    assert r.status_code == 200 and r.json()["role"] == "public"
    # 错误密码
    r = c.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert r.status_code == 401
    # 退出
    r = c.post("/api/auth/logout", json={"token": tok})
    assert r.status_code == 200
    r = c.get("/api/auth/me", params={"token": tok})
    assert r.status_code == 401
    print("auth OK")


def test_warning_levels_progression():
    c = _client()
    rank = {}
    for T in (2, 5, 10, 50, 100):
        r = c.get("/api/warning", params={"return_period": T})
        assert r.status_code == 200, r.text
        w = r.json()
        assert len(w["zones"]) == 9 and w["city_level"] in ("无", "蓝色", "黄色", "橙色", "红色")
        rank[T] = app._WARN_RANK[w["city_level"]]
    assert rank[2] <= rank[5] <= rank[10] <= rank[50] <= rank[100], rank
    assert rank[100] >= app._WARN_RANK["橙色"]
    print("warning OK", rank)


def test_impact_contains_loss():
    c = _client()
    r = c.get("/api/impact", params={"return_period": 100})
    im = r.json()
    assert im["estimated_loss_wan"] > 0
    assert sum(im["loss_by_type_wan"].values()) <= im["estimated_loss_wan"] * 1.01
    assert "loss_note" in im
    print("loss OK %.0f 万元" % im["estimated_loss_wan"])


def test_evacuation_stratified():
    c = _client()
    r = c.get("/api/evacuation", params={"return_period": 100})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert len(ev["shelters"]) >= 3
    assert isinstance(ev["routes"], list) and isinstance(ev.get("stranded", []), list)
    for rt in ev["routes"]:
        assert len(rt["points"]) >= 2 and rt["distance_m"] > 0
        assert rt["to"]["type"] in ("避难场所", "竖向避险(就地高层)")
        # 路径点落在研究区范围内
        lon0, lat0 = rt["points"][0]
        assert 113.28 < lon0 < 113.36 and 23.07 < lat0 < 23.15
    print("evacuation OK routes=%d stranded=%d" % (len(ev["routes"]), len(ev.get("stranded", []))))


def test_thematic_map_png():
    c = _client()
    r = c.get("/api/thematic_map", params={"return_period": 100})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) > 50_000
    r2 = c.get("/api/thematic_map", params={"return_period": 100, "title": "自定义标题专题图"})
    assert r2.status_code == 200 and r2.content[:8] == b"\x89PNG\r\n\x1a\n"
    print("thematic OK %d bytes" % len(r.content))


def test_assistant_intents():
    c = _client()
    for q, must in [("100年一遇淹多大?", "100年一遇"),
                    ("降雨300毫米会怎么样?", "模拟 300"),
                    ("哪里最容易涝?", "易涝"),
                    ("现在什么预警?", "预警"),
                    ("怎么疏散?", "避难"),
                    ("影响多少建筑?", "受影响建筑"),
                    ("经济损失多少?", "损失")]:
        r = c.post("/api/assistant", json={"question": q})
        assert r.status_code == 200, (q, r.text)
        d = r.json()
        assert must in d["answer"], (q, d["answer"])
    r = c.post("/api/assistant", json={"question": ""})
    assert r.status_code == 400
    print("assistant OK")


def test_realtime_rain():
    c = _client()
    r = c.get("/api/realtime_rain")
    d = r.json()
    assert r.status_code == 200
    assert len(d["stations"]) == 5 and len(d["trend_12h"]) == 12
    assert d["intensity_level"] in ("无雨", "小雨", "中雨", "大雨", "暴雨")
    # 确定性: 同一时间窗内两次请求一致
    d2 = c.get("/api/realtime_rain").json()
    assert d["city_rain24h_mm"] == d2["city_rain24h_mm"]
    print("realtime OK", d["intensity_level"], d["city_rain24h_mm"], "mm/24h")


def test_report_flow():
    c = _client()
    r = c.post("/api/report", json={"lon": 113.33, "lat": 23.11,
                                    "location_text": "测试点位(测试套件)",
                                    "depth_est": "0.3m", "desc": "测试上报, 可删除"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    r = c.get("/api/report")
    assert any(x["id"] == rid for x in r.json()["reports"])
    # 无 token 不可改状态
    r = c.post("/api/report/%s/status" % rid, json={"status": "已核实"})
    assert r.status_code == 401
    tok = _admin_token(c)
    r = c.post("/api/report/%s/status" % rid, params={"token": tok}, json={"status": "已核实"})
    assert r.status_code == 200 and r.json()["item"]["status"] == "已核实"
    # 非法状态
    r = c.post("/api/report/%s/status" % rid, params={"token": tok}, json={"status": "垃圾"})
    assert r.status_code == 400
    # 带照片上报
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    dataurl = "data:image/png;base64," + base64.b64encode(png).decode()
    r = c.post("/api/report", json={"location_text": "带图测试", "image": dataurl})
    assert r.status_code == 200, r.text
    rid2 = r.json()["id"]
    assert r.json()["item"].get("image"), "照片未保存"
    assert os.path.exists(os.path.join(ROOT, "reports", r.json()["item"]["image"]))
    r = c.get("/api/report")
    item2 = next(x for x in r.json()["reports"] if x["id"] == rid2)
    assert item2.get("image_url", "").startswith("/reports/")
    print("report OK")
