# -*- coding: utf-8 -*-
"""2026-10-06 全量代码审计回归测试:
REL-01 写盘故障 503 / REL-02 频控锁接线 / SEC-02 登录限流 / SEC-03 predict 截断+限流+400 /
SEC-04 订阅加固 / SEC-05 Bearer+日志打码 / BUG-01 必跑脚本 / ARC-01 影子常量(即 MISS-04~11)。
pytest 下经 conftest 隔离 store; 直接运行时自动重定向到系统临时目录。"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app
import store


def test_write_failure_returns_503_not_400():
    """MISS-04/REL-01: 写盘 OSError(磁盘满/Windows 杀软占用)必须 503, 不再伪装成 400。"""
    c = TestClient(app.app)
    orig = store._atomic_write_json

    def _boom(path, data):
        raise PermissionError(13, "Access is denied(模拟文件被占用/磁盘故障)")

    store._atomic_write_json = _boom
    try:
        r = c.post("/api/report", json={"location_text": "写盘故障演练"})
        assert r.status_code == 503, (r.status_code, r.text[:120])
        assert "字段格式" not in r.text, "不得把系统故障说成用户输入问题"
    finally:
        store._atomic_write_json = orig
    print("report 写盘 PermissionError → 503 OK")


def test_login_brute_force_rate_limited():
    """MISS-05/SEC-02: 连续失败登录第 6 次起必须 429(修复前 60 次错口令无一 429)。"""
    c = TestClient(app.app)
    codes = [c.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5, "前 5 次应正常拒绝: %s" % codes
    assert codes[5] == 429 and codes[6] == 429, "第 6/7 次应被限流: %s" % codes
    print("登录限流 5×401 + 2×429 OK")


def test_auth_bearer_header_supported():
    """SEC-05: token 优先取 Authorization: Bearer(不进访问日志/历史), query 仍兼容存量前端。"""
    c = TestClient(app.app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    r2 = c.get("/api/auth/me", headers={"Authorization": "Bearer " + tok})
    assert r2.status_code == 200 and r2.json()["role"] == "admin", r2.text
    r3 = c.get("/api/auth/me", params={"token": tok})
    assert r3.status_code == 200, "query token 兼容性破坏"
    r4 = c.get("/api/auth/me", headers={"x-auth-token": tok})
    assert r4.status_code == 200, "x-auth-token 头兼容性破坏"
    print("Bearer/x-auth-token/query 三路 token 均可鉴权 OK")


def test_subscribe_abuse_hardened():
    """MISS-06/SEC-04: 第 4 次订阅 429; 恶意邮箱入库前剥标签; 总量上限 409。"""
    c = TestClient(app.app)
    # 1) 频控: 3 次/60 秒, 第 4 次 429
    codes = [c.post("/api/subscribe", json={"email": "u%d@example.com" % i}).status_code
             for i in range(4)]
    assert codes[:3] == [200] * 3 and codes[3] == 429, codes
    # 2) 入库消毒: 带标签邮箱剥标签后存储, 库内不得出现 "<"
    store._report_rl.clear()
    r = c.post("/api/subscribe", json={"email": "<script>alert(1)</script>@x.com"})
    assert r.status_code == 200, r.text
    stored = [s["email"] for s in store._load_subscribers()]
    assert all("<" not in e for e in stored), stored
    # 3) 总量上限: 预填 500 条后新邮箱 → 409(绕开频控专测上限)
    store._report_rl.clear()
    orig = app._rate_ok
    app._rate_ok = lambda *a, **k: True
    try:
        store._save_subscribers([{"email": "f%d@x.com" % i, "zone": "全部", "created_at": "t"}
                                 for i in range(store.SUBSCRIBER_CAP)])
        r = c.post("/api/subscribe", json={"email": "new@x.com"})
        assert r.status_code == 409, (r.status_code, r.text[:120])
    finally:
        app._rate_ok = orig
    print("订阅 3×200+429 / 剥标签 / 上限 409 OK")


def test_predict_garbage_400_and_rate_limited():
    """SEC-03/PRED-01: 垃圾输入 400(此前 500); 每 IP 5 次/分钟, 第 6 次 429。"""
    c = TestClient(app.app)
    files = {"file": ("junk.tif", b"garbage-not-a-tiff", "image/tiff")}
    r = c.post("/api/predict", files=files)
    assert r.status_code == 400, (r.status_code, r.text[:120])
    codes = [c.post("/api/predict", files={"file": ("j.tif", b"x", "image/tiff")}).status_code
             for _ in range(5)]
    assert codes[-1] == 429, "第 6 次应被限流: %s" % codes
    print("predict 垃圾输入 400 + 限流 429 OK")


def test_rate_lock_is_wired():
    """MISS-10/REL-02: _rate_ok 必须 with _rate_lock(check-then-act 原子性不能靠 GIL 巧合)。"""
    import inspect
    assert "with _rate_lock" in inspect.getsource(store._rate_ok), \
        "_rate_ok 未持锁: 声明了 _rate_lock 却从未 acquire"
    print("_rate_lock 已接线 OK")


def test_token_redaction_filter():
    """MISS-11/SEC-05(进程内近似): uvicorn.access 挂打码过滤器, ?token= 原文不得进日志。"""
    import logging
    fl = logging.getLogger("uvicorn.access").filters
    assert any(isinstance(f, app._TokenRedactFilter) for f in fl), fl
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                            'GET /api/auth/me?token=PR0OF-TOKEN-LEAK HTTP/1.1" 401', (), None)
    assert app._TokenRedactFilter().filter(rec)
    assert "PR0OF-TOKEN-LEAK" not in rec.getMessage()
    assert "token=***" in rec.getMessage()
    print("访问日志 token 打码 OK")


def test_no_shadow_depth_threshold():
    """MISS-09/ARC-01: 淹没阈值/直方分界不得再硬编码 0.05——只能 import pipeline_config。"""
    import re
    pat = re.compile(r"depth\s*>\s*0\.05|\(\s*0\.05\s*,")
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [x for x in dn if x not in (".git", "__pycache__", ".tmp_read",
                                            "C2132_开发源码", "GF-FloodNet", "web",
                                            ".idea", "交付文档", "Build")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel == "pipeline_config.py" or rel.startswith("tests"):
                continue
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                s = line.strip()
                if pat.search(s) and not s.startswith("#") and '"""' not in s:
                    hits.append("%s:%d: %s" % (rel, i, s[:80]))
    assert not hits, "淹没阈值硬编码(应 import DEPTH_THRESH/DEPTH_BINS):\n" + "\n".join(hits)
    print("无影子阈值常量 OK")


def test_tile_flood_band_shared():
    """ARC-01d 守护: XYZ 瓦片色带必须复用 flood_render 唯一实现。
    tile_flood.py 曾自持 12 行渐变(改色带时瓦片静默保持旧配色), 阈值同源守不住这条。"""
    src = open(os.path.join(ROOT, "tools", "tile_flood.py"), encoding="utf-8").read()
    assert "from flood_render import" in src, "tile_flood 应 import flood_render 色带"
    for lit in ("166", "227", "255.0 - 153.0"):
        assert lit not in src, "tile_flood 仍自持色带字面量: %s" % lit
    print("tile_flood 色带已共享 flood_render OK")


def test_prep_design_storm_runs():
    """MISS-08/BUG-01: README 标注"必跑"的脚本必须能跑通(曾 NameError), 100 年≈300mm。
    (2026-10 工程化整理后位于 pipeline/ 子目录)"""
    import json as _json
    import subprocess
    r = subprocess.run([sys.executable, os.path.join("pipeline", "prep_design_storm.py")],
                       capture_output=True,
                       cwd=ROOT,
                       env=dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8"))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-300:]
    d = _json.load(open(os.path.join(ROOT, "flood_out", "design_storm_24h.json"),
                        encoding="utf-8"))
    assert abs(d["100"] - 300.6) <= 2.0, d
    print("prep_design_storm.py 复现 OK(100y=%s mm)" % d["100"])


def test_root_route_contract():
    """MISS-07: GET / 契约 — 重定向到 welcome.html(此前无任何测试守根路由)。"""
    c = TestClient(app.app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code in (301, 302, 307, 308), r.status_code
    assert r.headers["location"].endswith("/welcome.html"), r.headers.get("location")
    c2 = TestClient(app.app)
    r2 = c2.get("/")
    assert r2.status_code == 200 and "text/html" in r2.headers["content-type"]
    print("根路由 307→welcome OK")


if __name__ == "__main__":
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "c2132_audit_tests")
    os.makedirs(d, exist_ok=True)
    store._REPORT_DIR = d
    store._REPORTS_FILE = os.path.join(d, "reports.json")
    store._SUBSCRIBERS_FILE = os.path.join(d, "subscribers.json")
    store._AUTH_FILE = os.path.join(d, "web_users.json")
    app._REPORT_DIR = d
    app._tokens = {}
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            store._report_rl.clear()
            fn()
    print("audit regressions OK")
