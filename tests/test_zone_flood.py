# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app, rasterio, numpy as np

def test_zone_flood_shape_and_range():
    c = TestClient(app.app)
    r = c.get("/api/zone_flood", params={"return_period": 100, "grid": 3})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["grid"] == 3 and d["n"] == 9 and len(d["zones"]) == 9, d
    assert all(0.0 <= v <= 100.0 for v in d["zones"]), d
    print("zone_flood 100y zones:", d["zones"])

def test_zone_flood_monotonic():
    c = TestClient(app.app)
    prev = [0.0] * 9
    for T in (2, 5, 10, 50, 100):
        d = c.get("/api/zone_flood", params={"return_period": T, "grid": 3}).json()
        assert len(d["zones"]) == 9
        for k, v in enumerate(d["zones"]):
            assert v >= prev[k] - 1e-6, "格%d %d年(%.1f) 低于前一期(%.1f)" % (k, T, v, prev[k])
        prev = d["zones"]
    print("zone_flood 随重现期单调不减 OK")

def test_zone_flood_matches_raster():
    # 口径: 仅陆地淹没(排除河道), 占比 = 陆地淹没格 / 陆地格
    with rasterio.open("flood_out/flood_depth_100y.tif") as src:
        a = src.read(1).astype("float32")
    with rasterio.open("dem/study_dtm.tif") as src:
        z = src.read(1).astype("float32")
    z[np.isnan(z)] = 0.0
    d = app.zone_flood(return_period=100, grid=3)
    rstep, cstep = a.shape[0] // 3, a.shape[1] // 3
    land = z[rstep:2 * rstep, cstep:2 * cstep] > 0
    flood = (a[rstep:2 * rstep, cstep:2 * cstep] > 0) & land   # 中心格(区5)
    expect = round(100.0 * flood.sum() / land.sum(), 1)
    assert abs(d["zones"][4] - expect) < 1e-6, (d["zones"][4], expect)
    # 排除河道: 区5(含珠江)陆地淹没占比应远小于 depth>0/全区(曾≈44% 含河道)
    assert d["zones"][4] < 30, d
    print("zone_flood 与栅格手算一致(陆地口径) OK (区5=%.1f%%)" % expect)

def test_zone_flood_errors():
    c = TestClient(app.app)
    assert c.get("/api/zone_flood", params={"return_period": 999}).status_code == 422
    assert c.get("/api/zone_flood", params={"return_period": 100, "grid": 9}).status_code == 422
    print("zone_flood 参数校验 OK")

def test_index_html_has_zone_flood():
    html = open("index.html", encoding="utf-8").read()
    assert "/api/zone_flood" in html, "index.html 未引用 /api/zone_flood"
    assert "fetchZones" in html and "updateZones" in html and "colorByRatio" in html, "index.html 缺分区函数"
    print("index.html 分区代码存在 OK")

def test_index_html_no_instance_lerp():
    # Cesium.Color 只有静态 lerp(左,右,t), 没有实例 .lerp(); 实例调用会在浏览器抛 TypeError
    html = open("index.html", encoding="utf-8").read()
    assert ".lerp(" not in html, "index.html 仍使用不存在的 Color 实例 .lerp()"
    print("index.html 无实例 .lerp 依赖 OK")

def test_index_html_zone_grid_north_first():
    # 前端 3×3 分区网格须北排在前(i=0 北排, 用 north 锚定), 与后端栅格行序一致;
    # 若用 south 锚定(i=0 南排)会导致南北镜像、标签错位(区7/8 显示水体但占比低)
    html = open("index.html", encoding="utf-8").read()
    assert "north - (i + 1) * dh" in html, "前端分区行序未按北→南(i=0北排), 存在南北镜像"
    print("前端分区网格北排在前 OK")

if __name__ == "__main__":
    test_zone_flood_shape_and_range()
    test_zone_flood_monotonic()
    test_zone_flood_matches_raster()
    test_zone_flood_errors()
    test_index_html_has_zone_flood()
    test_index_html_no_instance_lerp()
    test_index_html_zone_grid_north_first()
    print("test_zone_flood OK")
