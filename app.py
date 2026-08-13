# -*- coding: utf-8 -*-
"""
app.py — 广东降雨洪涝 WebGIS 服务层(FastAPI)

架构: 数据层(PostGIS) -> 服务层(本文件) -> 表现层(flood.html / dashboard.html)

接口:
  GET  /api/health                          健康检查
  GET  /api/scenarios                       重现期场景(读 PostGIS flood_scenarios)
  GET  /api/flood_extent?return_period=100  淹没范围 GeoJSON(读 PostGIS flood_extent)
  GET  /api/flood_depth_png?return_period=100 水深色带 PNG
  POST /api/predict                         上传 5 波段影像 -> UNet 水体掩膜 PNG
  静态: /flood.html /dashboard.html 等前端文件

运行: uvicorn app:app --host 127.0.0.1 --port 8001
"""
import io, json, os
import numpy as np
import tifffile
import rasterio
from rasterio.windows import from_bounds
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 修复 rasterio 与系统(如 PostGIS)旧版 proj.db 冲突: 优先用 rasterio 自带 proj_data
_proj = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
if os.path.isdir(_proj):
    os.environ["PROJ_LIB"] = _proj
    os.environ["PROJ_DATA"] = _proj

try:
    import psycopg2
except ImportError:
    psycopg2 = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = dict(
    host=os.environ.get("FLOOD_DB_HOST", "localhost"),
    port=int(os.environ.get("FLOOD_DB_PORT", "5432")),
    dbname=os.environ.get("FLOOD_DB_NAME", "flood_analysis"),
    user=os.environ.get("FLOOD_DB_USER", "postgres"),
    password=os.environ.get("FLOOD_DB_PASSWORD", "123456"),
)
DEPTH_CAP = 6.0

# GeoScene / ArcGIS Online 服务(可选; 满足竞赛"不脱离GeoScene服务器端"要求)
GEOSCENE_EXTENT_URL = os.environ.get("GEOSCENE_EXTENT_URL", "").strip()
GEOSCENE_DEPTH_URL = os.environ.get("GEOSCENE_DEPTH_URL", "").strip()
GEOSCENE_ENABLED = bool(GEOSCENE_EXTENT_URL and GEOSCENE_DEPTH_URL)

app = FastAPI(title="广东降雨洪涝 WebGIS 服务层", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 未安装")
    return psycopg2.connect(**DB)


def colorize(depth):
    h, w = depth.shape
    img = np.zeros((h, w, 4), dtype=np.uint8)
    mask = depth > 0.05
    t = np.clip(depth / DEPTH_CAP, 0.0, 1.0)
    img[..., 0] = (166.0 * (1 - t)).astype("uint8")
    img[..., 1] = (227.0 - 176.0 * t).astype("uint8")
    img[..., 2] = (255.0 - 153.0 * t).astype("uint8")
    img[..., 3] = np.where(mask, 200, 0).astype("uint8")
    return Image.fromarray(img, "RGBA")


# ---------------- API ----------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "flood-webgis"}


@app.get("/api/scenarios")
def scenarios():
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute("""SELECT return_period_y, rain_mm, runoff_depth_m, water_level_m,
                                  max_depth_m, mean_depth_m, flooded_area_km2, flooded_cells, river_cells
                           FROM flood_scenarios ORDER BY return_period_y""")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        pass
    # 回退: 数据库不可用时读本地文件
    with open(os.path.join(ROOT, "flood_out", "scenarios.json"), encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/flood_extent")
def flood_extent(return_period: int = Query(100, ge=2, le=100)):
    try:
        with get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT ST_AsGeoJSON(geom) FROM flood_extent WHERE return_period_y=%s",
                        (return_period,))
            feats = [{"type": "Feature", "geometry": json.loads(r[0]), "properties": {}}
                     for r in cur.fetchall()]
        if feats:
            return {"type": "FeatureCollection", "features": feats}
    except Exception:
        pass
    # 回退: 读本地 geojson
    with open(os.path.join(ROOT, "flood_out", "flood_extent_%dy.geojson" % return_period),
              encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/flood_depth_png")
def flood_depth_png(return_period: int = Query(100, ge=2, le=100)):
    p = os.path.join(ROOT, "flood_out", "flood_depth_%dy.tif" % return_period)
    depth = tifffile.imread(p).astype("float32")
    buf = io.BytesIO()
    colorize(depth).save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/monthly_rain")
def monthly_rain():
    """研究区 5 年逐月降雨(mm), 供数据大屏「降雨态势」图。precip_tif 未随仓库分发时优雅回退。"""
    years = [2021, 2022, 2023, 2024, 2025]
    out = {}
    for yr in years:
        p = os.path.join(ROOT, "precip_tif", "precip_%d.tif" % yr)
        if not os.path.exists(p):
            return {"years": [], "months": [], "monthly_rain": {},
                    "note": "precip_tif 数据未分发, 降雨态势不可用"}
        with rasterio.open(p) as src:
            w = from_bounds(113.30, 23.09, 113.34, 23.13, src.transform).round_offsets().round_lengths()
            d = src.read(window=w).astype("float32")
        d[d == -32768] = np.nan
        out[yr] = [round(float(np.nanmedian(d[b])) * 0.1, 1) for b in range(12)]
    return {"years": years, "months": list(range(1, 13)), "monthly_rain": out}


@app.get("/api/depth_hist")
def depth_hist():
    """水深分布直方图 + 预警等级统计(100 年一遇)。"""
    d = tifffile.imread(os.path.join(ROOT, "flood_out", "flood_depth_100y.tif")).astype("float32")
    d = d[d > 0.05]
    bins = [(0.05, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 1e9)]
    labels = ["0-0.5m", "0.5-1m", "1-2m", "2-3m", "3-5m", ">5m"]
    counts = [int(((d > lo) & (d <= hi)).sum()) for lo, hi in bins]
    warn = {"蓝": int((d <= 0.5).sum()), "黄": int(((d > 0.5) & (d <= 1)).sum()),
            "橙": int(((d > 1) & (d <= 2)).sum()), "红": int((d > 2).sum())}
    return {"labels": labels, "counts": counts, "warn": warn}


@app.get("/api/geoscene")
def geoscene():
    """GeoScene/ArcGIS Online 服务配置。未配置时 enabled=false, 前端回退本地数据。"""
    return {
        "enabled": GEOSCENE_ENABLED,
        "extent_url": GEOSCENE_EXTENT_URL,
        "depth_url": GEOSCENE_DEPTH_URL,
        "note": "未配置时前端自动回退读取本地 flood_out/ 数据",
    }


@app.get("/api/realevent")
def realevent():
    """真实洪涝事件(北江2022-06英德) UNet 反演水深元数据。"""
    p = os.path.join(ROOT, "realevent_out", "realevent.json")
    if not os.path.exists(p):
        return JSONResponse({"error": "realevent 数据未生成，请先运行 realevent_beijiang.py"}, status_code=404)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/predict")
async def predict(file: UploadFile = File(...)):
    """UNet 水体提取: 上传 5 波段 GeoTIFF -> 水体二值掩膜 PNG"""
    ckpt = os.path.join(ROOT, "unet_out", "unet_water.pt")
    if not os.path.exists(ckpt):
        return JSONResponse({"error": "模型尚未训练完成"}, status_code=503)
    import torch
    from unet_model import UNet
    data = await file.read()
    I = tifffile.imread(io.BytesIO(data)).astype(np.float32)   # (H, W, 5)
    ck = torch.load(ckpt, map_location="cpu")
    size = ck["size"]; mean, std = ck["mean"], ck["std"]
    if I.shape[0] != size:
        I = np.stack([np.array(Image.fromarray(I[..., b].astype("uint16"), "I;16").resize((size, size)))
                      for b in range(5)], axis=2).astype(np.float32)
    X = I.transpose(2, 0, 1)
    for b in range(5):
        X[b] = (X[b] - mean[b]) / std[b]
    model = UNet(5, 1, base=ck["base"]); model.load_state_dict(ck["state_dict"]); model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(torch.from_numpy(X[None])))[0, 0].numpy()
    mask = (prob > 0.5).astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(mask, "L").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ---------------- 前端静态托管 ----------------
@app.get("/")
def root():
    return RedirectResponse("/welcome.html")


app.mount("/", StaticFiles(directory=ROOT, html=False), name="static")
