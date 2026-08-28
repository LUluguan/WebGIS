# -*- coding: utf-8 -*-
"""
app.py — 广东降雨洪涝 WebGIS 服务层(FastAPI)

架构: 数据层(PostGIS) -> 服务层(本文件) -> 表现层(index/dashboard/realevent/unet.html)

接口:
  GET  /api/health                          健康检查
  GET  /api/scenarios                       重现期场景(读 PostGIS flood_scenarios, 失败回退本地)
  GET  /api/flood_extent?return_period=100  淹没范围 GeoJSON
  GET  /api/flood_depth_png?return_period=100 水深色带 PNG(结果缓存)
  GET  /api/monthly_rain                    研究区逐月降雨(precip_tif 缺失时优雅回退)
  GET  /api/depth_hist?return_period=100    水深分布 + 预警等级(仅陆地, 排除河道)
  GET  /api/zone_flood?return_period=100    3×3 分区淹没占比(仅陆地)
  GET  /api/impact?return_period=100        淹没影响: 受影响建筑 + 人口(WorldPop)
  GET  /api/hotspots?return_period=100      易涝点 Top-N(按淹没面积排序)
  GET  /api/online_sim?rain_mm=200&c=0.5    在线模拟: 自定义雨量/径流系数实时反演
  GET  /api/realevent                       真实事件注册表(多事件)
  GET  /api/realevent/{event_id}            真实事件元数据(UNet 反演水深)
  GET  /api/realevent_extent?event=         真实事件淹没多边形(掩膜矢量化)
  GET  /api/geoscene                        GeoScene/ArcGIS Online 服务配置
  POST /api/predict                         上传 5 波段影像 -> UNet 水体掩膜 PNG
  静态: 前端页面/资源(白名单扩展名, 拒绝 .env/.pt/.npz 等敏感与大文件)

运行: uvicorn app:app --host 127.0.0.1 --port 8001
"""
import io, json, math, os
import numpy as np
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)
import tifffile
import rasterio
from rasterio.windows import from_bounds
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

ROOT = os.path.dirname(os.path.abspath(__file__))
# 读取 .env(可选): 提供 FLOOD_DB_* / GEOSCENE_* 配置; 缺失时用默认值并回退本地数据
if load_dotenv:
    load_dotenv(os.path.join(ROOT, ".env"))
DB = dict(
    host=os.environ.get("FLOOD_DB_HOST", "localhost"),
    port=int(os.environ.get("FLOOD_DB_PORT", "5432")),
    dbname=os.environ.get("FLOOD_DB_NAME", "flood_analysis"),
    user=os.environ.get("FLOOD_DB_USER", "postgres"),
    password=os.environ.get("FLOOD_DB_PASSWORD", "123456"),
)
DEPTH_CAP = 6.0
RUNOFF_COEF = 0.50      # 默认综合径流系数(与 bathtub_flood.py 一致, 可被请求参数覆盖)
DEPTH_THRESH = 0.05     # 淹没判定阈值(m)
POP_DENSITY = 23253.0   # 人/km² — 2020年七普天河区常住人口约224万/面积96.33km²(人口格网缺失时的均摊估算口径)

# GeoScene / ArcGIS Online 服务(可选; 满足竞赛"不脱离GeoScene服务器端"要求)
# 配置任一项即启用对应部分(发布至少一个服务即可形成对 GeoScene 服务器端的依赖)
GEOSCENE_EXTENT_URL = os.environ.get("GEOSCENE_EXTENT_URL", "").strip()
GEOSCENE_DEPTH_URL = os.environ.get("GEOSCENE_DEPTH_URL", "").strip()
GEOSCENE_ENABLED = bool(GEOSCENE_EXTENT_URL or GEOSCENE_DEPTH_URL)

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = FastAPI(title="广东降雨洪涝 WebGIS 服务层", version="1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 未安装")
    return psycopg2.connect(**DB)


def colorize(depth):
    h, w = depth.shape
    img = np.zeros((h, w, 4), dtype=np.uint8)
    mask = depth > DEPTH_THRESH
    t = np.clip(depth / DEPTH_CAP, 0.0, 1.0)
    img[..., 0] = (166.0 * (1 - t)).astype("uint8")
    img[..., 1] = (227.0 - 176.0 * t).astype("uint8")
    img[..., 2] = (255.0 - 153.0 * t).astype("uint8")
    img[..., 3] = np.where(mask, 200, 0).astype("uint8")
    return Image.fromarray(img, "RGBA")


# ==== 共享缓存: DTM / 水深PNG / 统计 / 建筑 / 人口(避免每请求重复 IO) ====
_dtm_cache = None
_png_cache = {}
_hist_cache = {}
_zone_cache = {}
_bld_cache = None
_pop_cache = None


def _get_dtm():
    """缓存读去建筑 DTM(study_dtm.tif), 与重现期场景同一份地形。返回 (z, transform)。"""
    global _dtm_cache
    if _dtm_cache is None:
        p = os.path.join(ROOT, "dem", "study_dtm.tif")
        if not os.path.exists(p):
            raise RuntimeError("study_dtm.tif 缺失, 请先运行 bathtub_flood.py")
        with rasterio.open(p) as src:
            z = src.read(1).astype("float32")
            transform = src.transform
        z[np.isnan(z)] = 0.0
        _dtm_cache = (z, transform)
    return _dtm_cache


def _load_depth_tif(return_period):
    p = os.path.join(ROOT, "flood_out", "flood_depth_%dy.tif" % return_period)
    if not os.path.exists(p):
        return None
    d = tifffile.imread(p).astype("float32")
    d[~np.isfinite(d)] = 0.0
    d[d < 0] = 0.0
    return d


def _bathtub(z, q):
    """陆域体积守恒: 求 W 使陆域(z>0)平均水深 = q。hi 上限 +q 避免特大暴雨 W 越界。"""
    land = z > 0
    lo, hi = 0.0, float(z.max()) + q
    for _ in range(60):
        W = 0.5 * (lo + hi)
        if float(np.clip(W - z[land], 0, None).mean()) < q:
            lo = W
        else:
            hi = W
    W = 0.5 * (lo + hi)
    return W, np.clip(W - z, 0, None)


def _cell_area_m2(transform, lat=23.11):
    return (transform.a * 111320.0 * math.cos(math.radians(lat))) * (abs(transform.e) * 110574.0)


def _zone_ratios(z, depth, grid=3):
    """grid×grid 分区陆地淹没占比(仅陆地, 排除河道), 与 /api/zone_flood 同算法。"""
    land = z > 0
    flood = (depth > 0) & land
    rows, cols = depth.shape
    rstep, cstep = max(1, rows // grid), max(1, cols // grid)
    zones = []
    for i in range(grid):
        rlo, rhi = i * rstep, min((i + 1) * rstep, rows)
        for j in range(grid):
            clo, chi = j * cstep, min((j + 1) * cstep, cols)
            blk_f = flood[rlo:rhi, clo:chi]
            blk_l = land[rlo:rhi, clo:chi]
            nl = int(blk_l.sum())
            zones.append(round(100.0 * (int(blk_f.sum()) / nl) if nl else 0.0, 1))
    return zones


# ==== 淹没影响: 受影响建筑 + 人口 ====
def _get_buildings():
    """缓存建筑(珠江新城 290 栋): 质心经纬度 + 名称 + 高度。"""
    global _bld_cache
    if _bld_cache is None:
        out = []
        p = os.path.join(ROOT, "gz_tower_buildings.geojson")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                gj = json.load(f)
            for feat in gj.get("features", []):
                g = feat.get("geometry")
                if not g or not g.get("coordinates"):
                    continue
                ring = g["coordinates"][0]
                lon = sum(pt[0] for pt in ring) / len(ring)
                lat = sum(pt[1] for pt in ring) / len(ring)
                pr = feat.get("properties", {}) or {}
                out.append({"name": pr.get("name") or "", "lon": float(lon),
                            "lat": float(lat), "height_m": float(pr.get("height") or 0)})
        _bld_cache = out
    return _bld_cache


def _get_pop():
    """缓存人口格网(fetch_pop.py 预生成, 已重采样到 DTM 网格); 缺失返回 None。"""
    global _pop_cache
    if _pop_cache is None:
        p = os.path.join(ROOT, "dem", "study_pop.tif")
        if os.path.exists(p):
            try:
                with rasterio.open(p) as src:
                    _pop_cache = src.read(1).astype("float32")
            except Exception:
                _pop_cache = None
        if _pop_cache is None:
            _pop_cache = False   # 标记"尝试过且无数据"
    return _pop_cache if _pop_cache is not False else None


def _sample_grid(arr, transform, lon, lat):
    """在网格 arr 上取 (lon,lat) 处的值; 越界返回 None。"""
    try:
        inv = ~transform
        col, row = inv * (lon, lat)
    except Exception:
        return None
    r, c = int(row), int(col)
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]:
        return float(arr[r, c])
    return None


def impact_stats(depth, transform):
    """受影响建筑(质心处水深>0.05m)与受影响人口。

    人口口径: 优先用 WorldPop 100m 人口格网(fetch_pop.py 产出 dem/study_pop.tif,
    掩膜内人口加和); 格网缺失时按天河区常住人口密度均摊估算——
    2020年七普天河区常住人口约224万/面积96.33km² ≈ 23,253人/km², 明确标注为估算。"""
    z, _ = _get_dtm()
    land = z > 0
    d_land = np.where(land, depth, 0.0)
    flood_m = d_land > DEPTH_THRESH
    blds = _get_buildings()
    affected = []
    for b in blds:
        d = _sample_grid(d_land, transform, b["lon"], b["lat"])
        if d is not None and d > DEPTH_THRESH:
            affected.append({"name": b["name"], "depth_m": round(d, 2),
                             "height_m": round(b["height_m"], 1)})
    affected.sort(key=lambda x: -x["depth_m"])
    flood_km2 = float(flood_m.sum()) * _cell_area_m2(transform) / 1e6
    pop = _get_pop()
    if pop is not None and pop.shape == depth.shape:
        v = pop[flood_m & np.isfinite(pop) & (pop > 0)]
        pop_affected, pop_src = int(v.sum()), "worldpop"
    else:
        pop_affected, pop_src = int(round(flood_km2 * POP_DENSITY)), "estimate"
    return {
        "affected_buildings": len(affected),
        "buildings_total": len(blds),
        "top_buildings": affected[:5],
        "flooded_land_km2": round(flood_km2, 3),
        "affected_population": pop_affected,
        "pop_source": pop_src,
    }


# ==== 易涝点 Top-N ====
def _ring_area_km2(ring, lat):
    """多边形面积(km²): 经纬度 shoelace + 纬度尺度校正。"""
    a = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2 * 111320.0 * math.cos(math.radians(lat)) * 110574.0 / 1e6


def hotspot_stats(flood_mask, depth, transform, top=8):
    """淹没斑块(已要求 flood_mask 为陆地淹没)按面积排序的 Top-N:
    面积 / 最大水深 / 平均水深 / 中心点与外包围盒(供前端定位)。"""
    if not flood_mask.any():
        return []
    from rasterio.features import shapes
    geoms = list(shapes(flood_mask.astype("uint8"), mask=flood_mask, transform=transform))
    items = []
    for g, v in geoms:
        if v != 1:
            continue
        ring = g["coordinates"][0]
        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        lat_c = sum(lats) / len(lats)
        # 栅格统计该斑块水深
        from rasterio.features import rasterize
        m = rasterize([(g, 1)], out_shape=depth.shape, transform=transform,
                      fill=0, dtype="uint8") > 0
        dv = depth[m & (depth > DEPTH_THRESH)]
        if dv.size == 0:
            continue
        items.append({
            "area_km2": round(_ring_area_km2(ring, lat_c), 4),
            "max_depth_m": round(float(dv.max()), 2),
            "mean_depth_m": round(float(dv.mean()), 2),
            "lon": round(float((min(lons) + max(lons)) / 2), 5),
            "lat": round(float((min(lats) + max(lats)) / 2), 5),
            "bbox": [round(min(lons), 5), round(min(lats), 5),
                     round(max(lons), 5), round(max(lats), 5)],
        })
        if len(items) > 400:   # 安全上限
            break
    items.sort(key=lambda x: -x["area_km2"])
    return items[:top]


# ==== 真实事件(多事件注册表) ====
def _event_registry():
    reg_p = os.path.join(ROOT, "realevent_out", "events.json")
    if os.path.exists(reg_p):
        try:
            with open(reg_p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 旧单事件布局兜底(产物在 realevent_out/ 根目录)
    if os.path.exists(os.path.join(ROOT, "realevent_out", "realevent.json")):
        return {"default": "yingde",
                "events": [{"id": "yingde", "name": "北江特大洪水(2022-06) · 英德城区", "dir": ""}]}
    return {"default": None, "events": []}


def _event_dir(event_id):
    reg = _event_registry()
    ev = next((e for e in reg["events"] if e["id"] == event_id), None)
    if ev is None:
        return None
    return os.path.join(ROOT, "realevent_out", ev.get("dir", ""))


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
    if return_period in _png_cache:
        return Response(content=_png_cache[return_period], media_type="image/png")
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    buf = io.BytesIO()
    colorize(d).save(buf, format="PNG")
    _png_cache[return_period] = buf.getvalue()
    return Response(content=_png_cache[return_period], media_type="image/png")


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
def depth_hist(return_period: int = Query(100, ge=2, le=100)):
    """水深分布直方图 + 预警等级统计(按重现期, 仅陆地淹没, 排除河道)。"""
    if return_period in _hist_cache:
        return _hist_cache[return_period]
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    # 排除河道(z<=0): 只统计陆地淹没水深, 与淹没范围一致, 避免河道深水扭曲分布
    z, _ = _get_dtm()
    d = d[(d > DEPTH_THRESH) & (z > 0)]
    bins = [(0.05, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 1e9)]
    labels = ["0-0.5m", "0.5-1m", "1-2m", "2-3m", "3-5m", ">5m"]
    counts = [int(((d > lo) & (d <= hi)).sum()) for lo, hi in bins]
    warn = {"蓝": int((d <= 0.5).sum()), "黄": int(((d > 0.5) & (d <= 1)).sum()),
            "橙": int(((d > 1) & (d <= 2)).sum()), "红": int((d > 2).sum())}
    res = {"return_period": return_period, "labels": labels, "counts": counts, "warn": warn}
    _hist_cache[return_period] = res
    return res


@app.get("/api/geoscene")
def geoscene():
    """GeoScene/ArcGIS Online 服务配置。未配置时 enabled=false, 前端回退本地数据。"""
    return {
        "enabled": GEOSCENE_ENABLED,
        "extent_url": GEOSCENE_EXTENT_URL,
        "depth_url": GEOSCENE_DEPTH_URL,
        "note": "未配置时前端自动回退读取本地 flood_out/ 数据",
    }


@app.get("/api/zone_flood")
def zone_flood(return_period: int = Query(100, ge=2, le=100), grid: int = Query(3, ge=2, le=4)):
    """grid×grid 网格分区淹没占比: "陆地淹没(depth>0且z>0)占陆地之比"。
    排除珠江河道(常年水体, 非淹没), 与淹没范围/水深分布口径一致。"""
    key = (return_period, grid)
    if key in _zone_cache:
        return _zone_cache[key]
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    z, _ = _get_dtm()
    res = {"return_period": return_period, "grid": grid, "n": grid * grid,
           "zones": _zone_ratios(z, d, grid)}
    _zone_cache[key] = res
    return res


@app.get("/api/impact")
def impact(return_period: int = Query(100, ge=2, le=100)):
    """淹没影响统计: 受影响建筑(质心处水深>0.05m, Top5 列出最深)与受影响人口(WorldPop)。"""
    try:
        d = _load_depth_tif(return_period)
        if d is None:
            return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
        _, transform = _get_dtm()
        return {"return_period": return_period, **impact_stats(d, transform)}
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.get("/api/hotspots")
def hotspots(return_period: int = Query(100, ge=2, le=100), top: int = Query(8, ge=1, le=20)):
    """易涝点 Top-N: 按淹没斑块面积排序(仅陆地淹没), 含最大/平均水深与定位 bbox。"""
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    z, transform = _get_dtm()
    flood = (d > DEPTH_THRESH) & (z > 0)
    return {"return_period": return_period, "hotspots": hotspot_stats(flood, d, transform, top)}


@app.get("/api/online_sim")
def online_sim(rain_mm: float = Query(..., gt=0, le=2000),
               c: float = Query(RUNOFF_COEF, ge=0.05, le=0.95),
               top: int = Query(5, ge=1, le=20)):
    """在线模拟: 输入 24h 雨量(mm)与综合径流系数 c(0.05-0.95, 海绵城市改造可降低 c)
    → 浴缸法实时反演水位/淹没范围/分区占比/影响统计/易涝点。默认 c 与重现期场景同口径。"""
    try:
        z, transform = _get_dtm()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    q = rain_mm / 1000.0 * c
    W, depth = _bathtub(z, q)
    land = z > 0
    flooded = (depth > DEPTH_THRESH) & land
    feats = []
    if flooded.any():
        from rasterio.features import shapes
        for g, v in shapes(flooded.astype("uint8"), mask=flooded, transform=transform):
            if v == 1:
                feats.append({"type": "Feature", "geometry": g, "properties": {}})
    area = _cell_area_m2(transform)
    return {
        "rain_mm": rain_mm, "c": round(c, 2),
        "water_level_m": round(float(W), 2),
        "runoff_depth_m": round(q, 4),
        "mean_depth_m": round(float(depth[flooded].mean()), 2) if flooded.any() else 0.0,
        "max_depth_m": round(float(depth[flooded].max()), 2) if flooded.any() else 0.0,
        "flooded_area_km2": round(float(flooded.sum() * area) / 1e6, 3),
        "flooded_cells": int(flooded.sum()),
        "zones": _zone_ratios(z, depth, 3),
        "impact": impact_stats(depth, transform),
        "hotspots": hotspot_stats(flooded, depth, transform, top),
        "extent": {"type": "FeatureCollection", "features": feats},
        "note": "浴缸法实时反演(默认 c=0.5 与重现期场景同口径; 自定义 c 为情景假设)",
    }


@app.get("/api/realevent")
def realevent_list():
    """真实事件注册表(多事件): [{id, name}] + 默认事件。"""
    reg = _event_registry()
    return {"default": reg["default"],
            "events": [{"id": e["id"], "name": e["name"]} for e in reg["events"]]}


@app.get("/api/realevent/{event_id}")
def realevent_meta(event_id: str):
    """真实事件元数据(UNet 反演水深)。event_id 见 /api/realevent。"""
    d = _event_dir(event_id)
    if d is None:
        return JSONResponse({"error": "未知事件 %s" % event_id}, status_code=404)
    p = os.path.join(d, "realevent.json")
    if not os.path.exists(p):
        return JSONResponse({"error": "事件数据未生成, 请先运行 realevent_beijiang.py --event %s" % event_id},
                            status_code=404)
    with open(p, encoding="utf-8") as f:
        meta = json.load(f)
    meta["dir"] = os.path.relpath(d, ROOT).replace("\\", "/")
    return meta


@app.get("/api/realevent_extent")
def realevent_extent(event: str = Query(None)):
    """真实事件 UNet 淹没范围矢量: 从 flood_mask.png 矢量化淹没多边形(4326)。
    供前端把三维水面裁剪成实际淹没形状。"""
    ev_id = event or _event_registry()["default"]
    if not ev_id:
        return JSONResponse({"error": "真实事件数据缺失"}, status_code=404)
    d = _event_dir(ev_id)
    if d is None:
        return JSONResponse({"error": "未知事件 %s" % ev_id}, status_code=404)
    mask_p = os.path.join(d, "flood_mask.png")
    meta_p = os.path.join(d, "realevent.json")
    if not os.path.exists(mask_p) or not os.path.exists(meta_p):
        return JSONResponse({"error": "真实事件数据缺失"}, status_code=404)
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    im = np.asarray(Image.open(mask_p))
    m = im > 128
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    west, south, east, north = meta["bbox"]
    rows, cols = m.shape
    tr = from_origin(west, north, (east - west) / cols, (north - south) / rows)
    feats = []
    for g, v in shapes(m.astype("uint8"), mask=m, transform=tr):
        if v == 1:
            feats.append({"type": "Feature", "geometry": g, "properties": {}})
    return {"type": "FeatureCollection", "features": feats}


@app.post("/api/predict")
async def predict(file: UploadFile = File(...)):
    """UNet 水体提取: 上传 5 波段 GeoTIFF -> 水体二值掩膜 PNG。
    归一化用 unet_apply.predict_mask 的 auto 策略(与真实事件管线同一路径)。"""
    ckpt = os.path.join(ROOT, "unet_out", "unet_water.pt")
    if not os.path.exists(ckpt):
        return JSONResponse({"error": "模型尚未训练完成"}, status_code=503)
    try:
        from unet_apply import load_model, predict_mask
        ck, _ = load_model(ckpt)
        data = await file.read()
        I = tifffile.imread(io.BytesIO(data)).astype(np.float32)   # (H, W, 5)
        if I.ndim != 3 or I.shape[2] < 5:
            return JSONResponse({"error": "输入应为 5 波段影像 (H,W,5)"}, status_code=400)
        mask = predict_mask(I[..., :5], ck["size"])
        buf = io.BytesIO()
        Image.fromarray(mask, "L").save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as e:
        return JSONResponse({"error": "推理失败: %s" % e}, status_code=500)


# ---------------- 前端静态托管(安全白名单) ----------------
@app.get("/")
def root():
    return RedirectResponse("/welcome.html")


_ALLOWED_EXT = {".html", ".htm", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                ".ico", ".json", ".geojson", ".wasm", ".ktx2", ".basis", ".cur",
                ".ttf", ".woff", ".woff2", ".tif", ".tiff"}


class SafeStaticFiles(StaticFiles):
    """静态服务白名单: 只允许前端资源扩展名, 拒绝点文件(.env/.git等)与
    模型/数据库/文档等敏感或大文件(.pt/.npz/.docx/.py/.bat...)被 HTTP 直接下载。"""

    def lookup_path(self, path):
        parts = [p for p in path.split("/") if p]
        if any(p.startswith(".") for p in parts):
            return "", None
        ext = os.path.splitext(path)[1].lower()
        if ext and ext not in _ALLOWED_EXT:
            return "", None
        return super().lookup_path(path)


app.mount("/", SafeStaticFiles(directory=ROOT, html=False), name="static")
