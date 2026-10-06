# -*- coding: utf-8 -*-
"""零覆盖端点补测(2026-10 审计): health / flood_depth_png / critical_assets /
predict(含 weights_only 加载路径) / subscribe(GET 管理面 + POST 去重)。"""
import io, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from fastapi.testclient import TestClient
import app

def test_health():
    c = TestClient(app.app)
    r = c.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok" and d["service"] == "flood-webgis", d
    print("health OK", d)

def test_flood_depth_png():
    c = TestClient(app.app)
    r = c.get("/api/flood_depth_png", params={"return_period": 100})
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG"
    # 陆域口径: 有色像元数应与栅格陆域淹没格一致(河道已排除)
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGBA"))
    import tifffile, rasterio
    d = tifffile.imread("flood_out/flood_depth_100y.tif").astype("float32")
    with rasterio.open("dem/study_dtm.tif") as s:
        z = s.read(1).astype("float32")
    expect = int(((d > 0.05) & (z > 0)).sum())
    assert int((im[..., 3] > 0).sum()) == expect, (int((im[..., 3] > 0).sum()), expect)
    print("flood_depth_png OK: 着色 %d 像元 == 陆域淹没格(河道排除)" % expect)

def test_critical_assets():
    c = TestClient(app.app)
    r = c.get("/api/critical_assets", params={"return_period": 100})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["total_public"] >= 1 and len(d["assets"]) == d["total_public"]
    assert "note" in d and "ground_m" in d["assets"][0], d["assets"][:1]
    # 排序: 受淹在前(按水深降序), 未受淹按地面高程升序
    fl = [a for a in d["assets"] if a["flooded"]]
    dr = [a for a in d["assets"] if not a["flooded"]]
    assert all(a["depth_m"] >= b["depth_m"] for a, b in zip(fl, fl[1:])), "受淹段应按水深降序"
    if len(dr) >= 2:
        assert all(a["ground_m"] <= b["ground_m"] for a, b in zip(dr, dr[1:])), "未受淹段应按地面高程升序"
    print("critical_assets OK: 公共 %d 栋, 受淹 %d 栋" % (d["total_public"], d["flooded"]))

def test_predict_upload():
    """合法 (C,H,W) 5 波段 GeoTIFF → 200 PNG(顺带覆盖 torch weights_only 加载);
    垃圾文件 → 400(2026-10 审计 SEC-03: 客户端输入错误, 旧版误归 500)且不含内部实现细节。"""
    import tifffile
    import numpy as np
    c = TestClient(app.app)
    buf = io.BytesIO()
    tifffile.imwrite(buf, np.random.default_rng(0).normal(100, 30, (5, 32, 32)).astype("float32"))
    r = c.post("/api/predict", files={"file": ("t.tif", buf.getvalue())})
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    r = c.post("/api/predict", files={"file": ("x.gif", b"GIF89a" + b"0" * 64)})
    assert r.status_code == 400
    assert "not a TIFF" not in r.text and "GIF8" not in r.text, "内部异常文本被回显"
    print("predict OK: 合法上传 200 PNG, 垃圾输入 400 无细节回显")

def test_subscribe_get_and_post():
    c = TestClient(app.app)
    # POST 正常登记
    r = c.post("/api/subscribe", json={"email": "cov@example.com", "zone": "区2"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    total1 = r.json()["total"]
    # 同邮箱重复登记不新增(总数不变)
    r = c.post("/api/subscribe", json={"email": "cov@example.com", "zone": "区2"})
    assert r.json()["total"] == total1
    # GET 无 token → 401; admin token → 200
    assert c.get("/api/subscribe").status_code == 401
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
    r = c.get("/api/subscribe", params={"token": tok})
    assert r.status_code == 200 and any(s["email"] == "cov@example.com" for s in r.json()["subscribers"])
    print("subscribe GET+POST OK: 去重/401/管理员列表")

if __name__ == "__main__":
    test_health()
    test_flood_depth_png()
    test_critical_assets()
    test_predict_upload()
    test_subscribe_get_and_post()
    print("test_zero_coverage OK")
