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
  GET  /api/impact?return_period=100        淹没影响: 受影响建筑 + 人口(WorldPop) + 经济损失估算
  GET  /api/hotspots?return_period=100      易涝点 Top-N(按淹没面积排序)
  GET  /api/online_sim?rain_mm=200&c=0.5    在线模拟: 自定义雨量/径流系数实时反演
  GET  /api/warning?return_period=100       分区预警等级(蓝/黄/橙/红) + 城市级预警发布
  GET  /api/evacuation?return_period=100    避难场所 + 疏散路径(A* 避水寻路)
  GET  /api/thematic_map?return_period=100  洪涝风险专题图 PNG(标题/图例/比例尺/指北针)
  GET  /api/realtime_rain                   实时雨情(演示数据, 每10分钟一情景)
  POST /api/assistant                       防汛智能问答(本地意图解析, 离线可用)
  GET  /api/report                          公众报汛列表
  POST /api/report                          公众报汛上报(可附照片)
  POST /api/report/{id}/status              报汛核实状态变更(需管理员)
  POST /api/auth/login                      用户登录(管理员/公众角色)
  GET  /api/auth/me                         当前登录用户
  POST /api/auth/logout                     退出登录
  GET  /api/realevent                       真实事件注册表(多事件)
  GET  /api/realevent/{event_id}            真实事件元数据(UNet 反演水深)
  GET  /api/realevent_extent?event=         真实事件淹没多边形(掩膜矢量化)
  GET  /api/geoscene                        GeoScene/ArcGIS Online 服务配置
  POST /api/predict                         上传 5 波段影像 -> UNet 水体掩膜 PNG
  静态: 前端页面/资源(白名单扩展名, 拒绝 .env/.pt/.npz 等敏感与大文件)

运行: uvicorn app:app --host 127.0.0.1 --port 8001
"""
import io, json, math, os, time, uuid, hashlib, random, datetime, base64
import numpy as np
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)
import tifffile
import rasterio
from rasterio.windows import from_bounds
from PIL import Image, ImageDraw
from fastapi import FastAPI, UploadFile, File, Query, Body
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


# ==== 淹没影响: 受影响建筑 + 人口 + 经济损失 ====
def _ring_area_m2(ring, lat):
    """多边形底面积(m²): 经纬度 shoelace + 纬度尺度校正。"""
    a = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2 * 111320.0 * math.cos(math.radians(lat)) * 110574.0


def _get_buildings():
    """缓存建筑(珠江新城 290 栋): 质心经纬度 + 名称 + 高度 + 底面积 + 类型。"""
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
                name = pr.get("name") or ""
                h = float(pr.get("height") or 0)
                out.append({"name": name, "lon": float(lon),
                            "lat": float(lat), "height_m": h,
                            "area_m2": round(_ring_area_m2(ring, lat), 1),
                            "btype": _building_type(name, h)})
        _bld_cache = out
    return _bld_cache


PUBLIC_KEYS = ("图书", "博物", "美术馆", "剧院", "体育", "学校", "医院", "政务", "文化", "会展")


def _building_type(name, height):
    """建筑类型判别(损失单价与避难场所筛选用): 名称关键词优先, 其次按高度分档。"""
    if any(k in name for k in PUBLIC_KEYS):
        return "公共"
    if height >= 100:
        return "商业综合体"
    if height >= 60:
        return "商办"
    return "住宅"


# 重置单价(元/m²建筑面积, 示例参数): 住宅/商办/商业综合体/公共
UNIT_COST = {"住宅": 8500, "商办": 10000, "商业综合体": 12000, "公共": 7000}


def _loss_rate(depth_m):
    """水深-损失率曲线(示例参数): 0.05m 起损, 2m 以上趋于饱和(0.85)。"""
    if depth_m <= DEPTH_THRESH:
        return 0.0
    return round(min(0.85, 0.10 + 0.28 * depth_m + 0.04 * depth_m * depth_m), 3)


def _building_loss(b, depth_m):
    """单栋建筑直接经济损失(万元) = 重置价值 × 损失率。重置价值 = 底面积×层数×单价。"""
    floors = max(1, round(b["height_m"] / 3.2)) if b["height_m"] > 0 else 6
    value = b["area_m2"] * floors * UNIT_COST.get(b["btype"], 8500)
    return value * _loss_rate(depth_m) / 1e4


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
    loss_total_wan = 0.0
    loss_by_type = {}
    for b in blds:
        d = _sample_grid(d_land, transform, b["lon"], b["lat"])
        if d is not None and d > DEPTH_THRESH:
            loss_wan = _building_loss(b, d)
            loss_total_wan += loss_wan
            loss_by_type[b["btype"]] = loss_by_type.get(b["btype"], 0.0) + loss_wan
            affected.append({"name": b["name"], "depth_m": round(d, 2),
                             "height_m": round(b["height_m"], 1),
                             "btype": b["btype"], "loss_wan": round(loss_wan, 1)})
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
        "estimated_loss_wan": round(loss_total_wan, 1),
        "loss_by_type_wan": {k: round(v, 1) for k, v in loss_by_type.items()},
        "loss_note": "直接经济损失估算: 底面积×层数×重置单价×水深损失率曲线(示例参数, 仅建筑直接损失)",
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


def _online_sim_core(rain_mm, c, top=5):
    """在线模拟核心逻辑(供端点与问答复用; 参数须为普通数值)。"""
    z, transform = _get_dtm()
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


@app.get("/api/online_sim")
def online_sim(rain_mm: float = Query(..., gt=0, le=2000),
               c: float = Query(RUNOFF_COEF, ge=0.05, le=0.95),
               top: int = Query(5, ge=1, le=20)):
    """在线模拟: 输入 24h 雨量(mm)与综合径流系数 c(0.05-0.95, 海绵城市改造可降低 c)
    → 浴缸法实时反演水位/淹没范围/分区占比/影响统计/易涝点。默认 c 与重现期场景同口径。"""
    try:
        return _online_sim_core(rain_mm, c, top)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


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


# ================= 复赛增强: 预警 / 疏散 / 专题图 / 问答 / 雨情 / 报汛 / 认证 =================

# ==== 用户认证(轻量: 文件用户表 + 内存 token, 管理员/公众两角色) ====
_AUTH_FILE = os.path.join(ROOT, "web_users.json")
_tokens = {}   # token -> {username, role, ts}


def _hash_pw(pw, salt):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def _load_users():
    """用户表缺失时自动播种演示账号: admin/admin123(管理员), public/123456(公众)。"""
    if not os.path.exists(_AUTH_FILE):
        users = []
        for uname, pw, role in (("admin", "admin123", "admin"), ("public", "123456", "public")):
            salt = uuid.uuid4().hex[:12]
            users.append({"username": uname, "salt": salt,
                          "hash": _hash_pw(pw, salt), "role": role})
        with open(_AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=1)
    with open(_AUTH_FILE, encoding="utf-8") as f:
        return json.load(f)


def _current_user(req_token):
    if not req_token:
        return None
    t = _tokens.get(req_token)
    if not t:
        return None
    if time.time() - t["ts"] > 12 * 3600:   # 12h 过期
        _tokens.pop(req_token, None)
        return None
    return {"username": t["username"], "role": t["role"]}


def _require_admin(req_token):
    u = _current_user(req_token)
    if not u or u["role"] != "admin":
        return None
    return u


@app.post("/api/auth/login")
def auth_login(payload: dict = Body(...)):
    uname = str(payload.get("username", "")).strip()
    pw = str(payload.get("password", ""))
    for u in _load_users():
        if u["username"] == uname and u["hash"] == _hash_pw(pw, u["salt"]):
            tok = uuid.uuid4().hex
            _tokens[tok] = {"username": uname, "role": u["role"], "ts": time.time()}
            return {"token": tok, "username": uname, "role": u["role"]}
    return JSONResponse({"error": "用户名或密码错误"}, status_code=401)


@app.get("/api/auth/me")
def auth_me(token: str = Query(None)):
    u = _current_user(token)
    if not u:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return u


@app.post("/api/auth/logout")
def auth_logout(payload: dict = Body(...)):
    _tokens.pop(str(payload.get("token", "")), None)
    return {"ok": True}


# ==== 预警等级(分区蓝/黄/橙/红 + 城市级预警发布) ====
_WARN_RANK = {"无": 0, "蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}
_WARN_STYLE = {
    "蓝色": {"color": "#42a5f5", "advice": "关注积水, 低洼路段谨慎通行"},
    "黄色": {"color": "#ffd54f", "advice": "避免进入地下车库/下穿隧道, 出行避开易涝点"},
    "橙色": {"color": "#ff8a65", "advice": "减少外出, 地下空间停用, 低洼人员做好转移准备"},
    "红色": {"color": "#ef5350", "advice": "立即转移低洼与地下空间人员, 实施交通管制, 开放应急避难场所"},
}
_warn_cache = {}


def _zone_depth_stats(z, depth, grid=3):
    """分区(陆地)淹没占比 + 最大水深 + 平均水深。"""
    land = z > 0
    rows, cols = depth.shape
    rstep, cstep = max(1, rows // grid), max(1, cols // grid)
    out = []
    for i in range(grid):
        rlo, rhi = i * rstep, min((i + 1) * rstep, rows)
        for j in range(grid):
            clo, chi = j * cstep, min((j + 1) * cstep, cols)
            blk_d = depth[rlo:rhi, clo:chi]
            blk_l = land[rlo:rhi, clo:chi]
            dv = blk_d[blk_l & (blk_d > DEPTH_THRESH)]
            nl = int(blk_l.sum())
            ratio = 100.0 * dv.size / nl if nl else 0.0
            out.append({"ratio": round(ratio, 1),
                        "max": round(float(dv.max()), 2) if dv.size else 0.0,
                        "mean": round(float(dv.mean()), 2) if dv.size else 0.0})
    return out


def _zone_level(st):
    """单区预警等级: 按该区陆地淹没占比分档(片区地势低洼, 水深指标不敏感, 以面积占比为准)。"""
    r = st["ratio"]
    if r >= 20:
        return "红色"
    if r >= 14:
        return "橙色"
    if r >= 8:
        return "黄色"
    if r >= 3:
        return "蓝色"
    return "无"


# 城市级预警阈值(按陆域淹没面积 km²): 该片区低洼, 2年一遇即有明显淹没
_CITY_AREAS = [(1.60, "红色"), (1.30, "橙色"), (1.00, "黄色"), (0.60, "蓝色")]


def _city_level_by_area(area_km2):
    for th, lv in _CITY_AREAS:
        if area_km2 >= th:
            return lv
    return "无"


def _compute_warning(z, depth, label):
    stats = _zone_depth_stats(z, depth, 3)
    zones = []
    for k, st in enumerate(stats):
        lv = _zone_level(st)
        zones.append({"zone": k + 1, "level": lv, "ratio_pct": st["ratio"],
                      "max_depth_m": st["max"], "mean_depth_m": st["mean"]})
    land = z > 0
    flooded = (depth > DEPTH_THRESH) & land
    area_km2 = round(float(flooded.sum()) * _cell_area_m2(_get_dtm()[1]) / 1e6, 3)
    city = _city_level_by_area(area_km2)
    style = _WARN_STYLE.get(city, {"color": "#9fb3cc", "advice": "正常状态, 保持关注"})
    return {
        "scenario": label, "city_level": city, "city_rank": _WARN_RANK[city],
        "color": style["color"], "advice": style["advice"],
        "flooded_area_km2": area_km2,
        "zones": zones,
        "message": "珠江新城片区分级预警: %s" % (city if city != "无" else "无预警"),
        "published_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": "城区: 淹没面积≥0.6/1.0/1.3/1.6km² → 蓝/黄/橙/红; 分区: 淹没占比≥8%/18%/28%/40%",
        "note": "预警等级由淹没模拟结果自动分级, 供演示。真实业务需衔接三防部门发布流程。",
    }


@app.get("/api/warning")
def warning(return_period: int = Query(100, ge=2, le=100)):
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    key = ("T", return_period)
    if key not in _warn_cache:
        z, _ = _get_dtm()
        _warn_cache[key] = _compute_warning(z, d, "%d年一遇" % return_period)
    return _warn_cache[key]


# ==== 避难场所 + 疏散路径(A* 避水寻路) ====
_evac_cache = {}
_SHELTER_KEYS = PUBLIC_KEYS


def _pick_shelters(d_land, k=6):
    """避难场所: 未受淹建筑中优先公共设施(图书馆/体育/学校…), 不足补高层(竖向避难)。
    在候选中按"最远点采样"取 k 个, 保证空间分散。"""
    blds = _get_buildings()
    dry_pub, dry_high = [], []
    for b in blds:
        if b["depth"] is not None and b["depth"] > DEPTH_THRESH:
            continue
        if any(key in b["name"] for key in _SHELTER_KEYS):
            dry_pub.append(b)
        elif b["height_m"] >= 30:
            dry_high.append(b)
    cand = sorted(dry_pub, key=lambda b: -b["height_m"]) + \
        sorted(dry_high, key=lambda b: -b["height_m"])
    if not cand:
        return []
    picked = [cand[0]]
    while len(picked) < k and len(picked) < len(cand):
        best, best_d = None, -1.0
        for b in cand:
            if b in picked:
                continue
            dmin = min((b["lon"] - p["lon"]) ** 2 + (b["lat"] - p["lat"]) ** 2 for p in picked)
            if dmin > best_d:
                best_d, best = dmin, b
        picked.append(best)
    return [{"name": b["name"] or ("避难建筑%d" % (i + 1)),
             "lon": b["lon"], "lat": b["lat"],
             "type": "公共设施避难" if any(key in b["name"] for key in _SHELTER_KEYS) else "高层竖向避难",
             "height_m": b["height_m"]}
            for i, b in enumerate(picked)]


def _astar(blocked, start, goal, cost=None):
    """8 邻域 A*(成本感知: cost(r,c) 返回通过该格的相对代价)。
    blocked 为布尔栅格(True=不可通行)。返回栅格路径或 None。"""
    import heapq
    rows, cols = blocked.shape
    if blocked[start]:
        return None
    _cost = cost or (lambda r, c: 1.0)

    def h(a, b):
        dr, dc = abs(a[0] - b[0]), abs(a[1] - b[1])
        return max(dr, dc) + 0.414 * min(dr, dc)

    g = {start: 0.0}
    came = {}
    pq = [(h(start, goal), 0.0, start)]
    while pq:
        _, gs, cur = heapq.heappop(pq)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1]
        if gs > g.get(cur, 1e18):
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = cur[0] + dr, cur[1] + dc
                if not (0 <= nr < rows and 0 <= nc < cols) or blocked[nr, nc]:
                    continue
                ng = gs + (1.414 if (dr and dc) else 1.0) * _cost(nr, nc)
                if ng < g.get((nr, nc), 1e18):
                    g[(nr, nc)] = ng
                    came[(nr, nc)] = cur
                    heapq.heappush(pq, (ng + h((nr, nc), goal), ng, (nr, nc)))
    return None


def _nearest_cell(mask, r, c, max_ring=60):
    """从 (r,c) 螺旋外扩找最近的 True 格(如最近的干燥格)。"""
    rows, cols = mask.shape
    if mask[r, c]:
        return r, c
    for ring in range(1, max_ring):
        for dr in range(-ring, ring + 1):
            for dc in (-ring, ring):
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc]:
                    return nr, nc
        for dc in range(-ring + 1, ring):
            for dr in (-ring, ring):
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc]:
                    return nr, nc
    return None


def _island_labels(blocked):
    """8 邻域连通域标记: 返回 (lab 栅格, sizes)。深水与河道将可通行区切成孤岛。"""
    from collections import deque
    rows, cols = blocked.shape
    lab = np.full((rows, cols), -1, dtype=np.int32)
    sizes = []
    for r0 in range(rows):
        for c0 in range(cols):
            if blocked[r0, c0] or lab[r0, c0] >= 0:
                continue
            lid = len(sizes)
            n = 0
            dq = deque([(r0, c0)])
            lab[r0, c0] = lid
            while dq:
                r, c = dq.popleft()
                n += 1
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols and lab[nr, nc] < 0 and not blocked[nr, nc]:
                            lab[nr, nc] = lid
                            dq.append((nr, nc))
            sizes.append(n)
    return lab, sizes


def _evacuation_core(return_period, max_routes=4):
    """避难场所与疏散路径核心逻辑(供端点与问答复用)。
    分层策略: 同岛避难场所(步行) → 同岛干燥高层(竖向避险) → 孤岛标记待救援。"""
    key = (return_period, max_routes)
    if key in _evac_cache:
        return _evac_cache[key]
    d = _load_depth_tif(return_period)
    if d is None:
        return None
    z, transform = _get_dtm()
    d_land = np.where(z > 0, d, 0.0)
    for b in _get_buildings():
        b["depth"] = _sample_grid(d_land, transform, b["lon"], b["lat"])
    shelters = _pick_shelters(d_land, 6)
    if not shelters:
        for b in _get_buildings():
            b["depth"] = None
        return {"return_period": return_period, "shelters": [], "routes": [], "stranded": [],
                "walk_rule": "网格 A* 成本感知寻路",
                "note": "当前情景下无干燥公共/高层建筑可作避难场所(全域高淹没)。"}
    hs = hotspot_stats((d > DEPTH_THRESH) & (z > 0), d, transform, top=max_routes)
    # 网格化: ≥0.8m 不可通行(成人涉水极限); 0.5–0.8m 高风险×6, 0.15–0.5m 涉水×3
    blocked = (d >= 0.8) | (z <= 0)
    wade = np.where(d >= 0.5, 6.0, np.where(d > 0.15, 3.0, 1.0))
    lab, _sizes = _island_labels(blocked)
    inv = ~transform
    cell_m = _cell_area_m2(transform) ** 0.5

    def cell_of(lon, lat):
        c, r = inv * (lon, lat)
        return int(r), int(c)

    def island_of(lon, lat):
        r, c = cell_of(lon, lat)
        rr, cc = _nearest_cell(~blocked, r, c, 40)
        return int(lab[rr, cc]) if rr is not None else -1

    sh_isl = [(sh, island_of(sh["lon"], sh["lat"])) for sh in shelters]
    # 竖向避险候选: 干燥建筑按岛分组, 每岛取最高的 8 栋(保证每个有建筑的孤岛都有候选)
    vert_by_isl = {}
    for b in sorted([b for b in _get_buildings()
                     if b["depth"] is not None and b["depth"] <= 0.3],
                    key=lambda x: -x["height_m"]):
        i2 = island_of(b["lon"], b["lat"])
        lst = vert_by_isl.setdefault(i2, [])
        if len(lst) < 8:
            lst.append(b)

    routes, stranded = [], []
    for h in hs:
        sr, sc = cell_of(h["lon"], h["lat"])
        dry0 = _nearest_cell(~blocked, sr, sc, 40)
        if dry0 is None:
            stranded.append({"lon": h["lon"], "lat": h["lat"], "area_km2": h["area_km2"],
                             "reason": "易涝点完全被≥0.8m深水包围(孤岛), 建议舟艇救援或待援"})
            continue
        isl = int(lab[dry0])
        tgts = [(sh, "避难场所") for sh, i2 in sh_isl if i2 == isl] + \
               [(b, "竖向避险(就地高层)") for b in vert_by_isl.get(isl, [])]
        best = None
        for tgt, kind in tgts:
            gr, gc = cell_of(tgt["lon"], tgt["lat"])
            gc_cell = _nearest_cell(~blocked, gr, gc, 40)
            if gc_cell is None:
                continue
            path = _astar(blocked, dry0, gc_cell, cost=lambda r, c: wade[r, c])
            if not path:
                continue
            dist = sum(cell_m * (1.414 if (abs(path[i][0] - path[i + 1][0]) +
                                           abs(path[i][1] - path[i + 1][1])) == 2 else 1.0)
                       for i in range(len(path) - 1))
            score = (0 if kind == "避难场所" else 1, dist)
            if best is None or score < best[0]:
                best = (score, tgt, kind, path, dist)
        if best:
            _, tgt, kind, path, dist = best
            step = max(1, len(path) // 60)
            pts = path[::step]
            if path[-1] != pts[-1]:
                pts.append(path[-1])
            pts_t = []
            for r, c in pts:
                lon, lat = transform * (c + 0.5, r + 0.5)
                pts_t.append([round(float(lon), 6), round(float(lat), 6)])
            routes.append({
                "from": {"lon": h["lon"], "lat": h["lat"],
                         "area_km2": h["area_km2"], "max_depth_m": h["max_depth_m"]},
                "to": {"name": tgt.get("name") or "高层建筑(竖向避险)", "type": kind,
                       "lon": tgt["lon"], "lat": tgt["lat"]},
                "distance_m": int(dist),
                "points": pts_t,
            })
        else:
            stranded.append({"lon": h["lon"], "lat": h["lat"], "area_km2": h["area_km2"],
                             "reason": "所在陆域孤岛内无干燥高层建筑, 建议舟艇救援"})
    for b in _get_buildings():
        b["depth"] = None   # 清理临时字段
    res = {"return_period": return_period,
           "shelters": shelters, "routes": routes, "stranded": stranded,
           "walk_rule": "网格 A* 成本感知寻路: 水深≥0.8m 不可通行(涉水极限), 0.5–0.8m 高风险(×6), 0.15–0.5m 涉水(×3)",
           "strategy": "同岛避难场所(步行优先) → 同岛干燥高层(竖向避险) → 孤岛标记待救援",
           "note": "避难场所由未受淹公共设施/高层建筑自动筛选(示例), 供应急决策演示。"}
    _evac_cache[key] = res
    return res


@app.get("/api/evacuation")
def evacuation(return_period: int = Query(100, ge=2, le=100),
               max_routes: int = Query(4, ge=1, le=8)):
    """避难场所与疏散路径: 主要易涝点 → 最近可达避难场所, A* 网格寻路(深水阻断/浅水涉水)。"""
    try:
        res = _evacuation_core(return_period, max_routes)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    if res is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    return res


# ==== 洪涝风险专题图 PNG(纯 PIL 出图: 标题/图例/比例尺/指北针/落款, 无 matplotlib 依赖) ====
_theme_cache = {}

_THEME_BINS = [(0.05, 0.5, "#9be3ff", "0.05–0.5"), (0.5, 1.0, "#4fc3f7", "0.5–1.0"),
               (1.0, 2.0, "#2196f3", "1.0–2.0"), (2.0, 3.0, "#0d47a1", "2.0–3.0"),
               (3.0, 99.0, "#4a148c", ">3.0")]


def _load_cjk_font(size):
    from PIL import ImageFont
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
               r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


@app.get("/api/thematic_map")
def thematic_map(return_period: int = Query(100, ge=2, le=100),
                 title: str = Query(None, max_length=60)):
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    key = (return_period, title or "")
    if key in _theme_cache:
        return Response(content=_theme_cache[key], media_type="image/png")
    z, transform = _get_dtm()
    rows, cols = d.shape
    west = transform.c
    north = transform.f
    east = west + transform.a * cols
    south = north + transform.e * rows

    # 画布: 数据区保持宽高比, 右侧留图例栏, 上下留标题/落款
    W, H = 1430, 1000
    MAP_L, MAP_T, MAP_R, MAP_B = 70, 110, 1180, 880
    img = Image.new("RGB", (W, H), "#f5f7fa")
    px = img.load()
    draw = ImageDraw.Draw(img)
    # ---- 地形晕渲底图(低分辨率平滑明暗, 突出淹没层) + 淹没着色 ----
    mw, mh = MAP_R - MAP_L, MAP_B - MAP_T
    SW, SH = 72, 72
    z_small = np.array(Image.fromarray(
        np.clip((z / max(1.0, float(np.nanmax(z))) * 255), 0, 255).astype("uint8"), "L"
    ).resize((SW, SH), Image.BILINEAR)).astype("float32") / 255.0 * max(1.0, float(np.nanmax(z)))
    gz = np.gradient(z_small)
    shade_small = np.clip(0.80 + 9.0 * (gz[0] + gz[1]), 0.62, 1.0)
    shade_img = Image.fromarray((shade_small * 255).astype("uint8"), "L").resize((mw, mh), Image.BILINEAR).convert("RGB")
    flood_rgb = np.zeros((rows, cols, 3), dtype="uint8")
    flood_any = np.zeros((rows, cols), dtype=bool)
    for lo, hi, col, _lbl in _THEME_BINS:
        m = (d > lo) & (d <= hi)
        r8, g8, b8 = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
        flood_rgb[m] = (r8, g8, b8)
        flood_any |= m
    fl_img = Image.fromarray(flood_rgb, "RGB").resize((mw, mh), Image.NEAREST)
    shade_px, fl_px = shade_img.load(), fl_img.load()
    for yy in range(mh):
        for xx in range(mw):
            fr, fg, fb = fl_px[xx, yy]
            if fr or fg or fb:   # 有淹没: 以淹没色为主, 保留 15% 地形明暗
                sr = shade_px[xx, yy][0] / 255.0
                px[MAP_L + xx, MAP_T + yy] = (int(fr * (0.85 + 0.15 * sr)),
                                              int(fg * (0.85 + 0.15 * sr)),
                                              int(fb * (0.85 + 0.15 * sr)))
            else:
                s = shade_px[xx, yy]
                px[MAP_L + xx, MAP_T + yy] = (int(s[0] * 0.30 + 0.70 * 226),
                                              int(s[1] * 0.30 + 0.70 * 233),
                                              int(s[2] * 0.30 + 0.70 * 240))
    draw.rectangle([MAP_L, MAP_T, MAP_R, MAP_B], outline="#37474f", width=2)
    # ---- 3×3 分区虚线网格 ----
    for i in (1, 2):
        gx = MAP_L + int(mw * i / 3)
        for yy in range(MAP_T, MAP_B, 7):
            draw.line([(gx, yy), (gx, min(yy + 4, MAP_B))], fill="#607d8b", width=1)
        gy = MAP_T + int(mh * i / 3)
        for xx in range(MAP_L, MAP_R, 7):
            draw.line([(xx, gy), (min(xx + 4, MAP_R), gy)], fill="#607d8b", width=1)
    # ---- 标题 / 眉头 / 落款 ----
    f_title = _load_cjk_font(26)
    f_med = _load_cjk_font(15)
    f_sm = _load_cjk_font(12)
    ttl = title or ("珠江新城 %d 年一遇暴雨洪涝风险专题图" % return_period)
    draw.text((MAP_L, 40), ttl, font=f_title, fill="#102a43")
    draw.text((MAP_L, 14), "C2132 · 基于WebGIS的三维城市降雨洪涝可视化表达", font=f_med, fill="#486581")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    draw.text((MAP_L, MAP_B + 16), "制图: 浴缸法体积注水反演(仅陆域, 排除珠江河道) · 数据: 情景模拟结果, 仅供演示 · 制图时间: " + now,
              font=f_sm, fill="#627d98")
    draw.text((MAP_L, MAP_B + 38), "坐标系: WGS84 经纬度 · 分区: 3×3 网格 · 深色为珠江河道掩膜区", font=f_sm, fill="#9fb3c8")
    # ---- 指北针 ----
    nx, ny = MAP_R - 40, MAP_T + 56
    draw.polygon([(nx, ny - 34), (nx - 9, ny + 8), (nx, ny - 2)], fill="#263238")
    draw.polygon([(nx, ny - 34), (nx + 9, ny + 8), (nx, ny - 2)], fill="#b0bec5")
    draw.text((nx - 6, ny + 12), "N", font=f_med, fill="#263238")
    # ---- 比例尺(约1km) ----
    sx0, sy = MAP_L + 24, MAP_B - 34
    sb_px = int(0.01 / (transform.a))   # 0.01°≈1km → 像素
    draw.line([(sx0, sy), (sx0 + sb_px, sy)], fill="#263238", width=5)
    draw.line([(sx0, sy - 6), (sx0, sy + 6)], fill="#263238", width=3)
    draw.line([(sx0 + sb_px, sy - 6), (sx0 + sb_px, sy + 6)], fill="#263238", width=3)
    draw.text((sx0 + sb_px // 2 - 26, sy - 30), "约 1 km", font=f_med, fill="#263238")
    # ---- 图例(右侧栏) ----
    lx = MAP_R + 36
    draw.text((lx, MAP_T + 4), "淹没水深 (m)", font=f_med, fill="#102a43")
    yy = MAP_T + 40
    for lo, hi, col, lbl in _THEME_BINS:
        draw.rectangle([lx, yy, lx + 34, yy + 22], fill=col, outline="#78909c")
        draw.text((lx + 44, yy + 3), lbl, font=f_med, fill="#334e68")
        yy += 36
    yy += 18
    draw.text((lx, yy), "底图: DTM 地形晕渲", font=f_sm, fill="#627d98")
    yy += 24
    draw.text((lx, yy), "虚线: 3×3 分区", font=f_sm, fill="#627d98")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    _theme_cache[key] = buf.getvalue()
    return Response(content=_theme_cache[key], media_type="image/png")


# ==== 实时雨情(演示数据: 每10分钟一个确定性情景, 可替换为真实气象接口) ====
_STATIONS = [("猎德大道站", 113.3230, 23.1125), ("花城大道站", 113.3295, 23.1170),
             ("珠江公园站", 113.3355, 23.1195), ("海心沙站", 113.3180, 23.1095),
             ("员村站", 113.3420, 23.1135)]


@app.get("/api/realtime_rain")
def realtime_rain():
    slot = int(time.time() // 600)
    rng = random.Random(slot * 7919)
    # 过去24h逐时雨量: 30%概率处于一场降雨过程中(前强后弱), 否则平稳
    in_storm = rng.random() < 0.45
    hours = []
    for i in range(24):
        if in_storm:
            peak = 22 - i * 0.7     # 当前时次最强, 向过去递减
            hours.append(max(0.0, rng.gauss(max(0.8, peak), 3.0)) if i >= 18 else max(0.0, rng.gauss(1.2, 1.5)))
        else:
            hours.append(max(0.0, rng.gauss(0.8, 1.4)) if rng.random() < 0.4 else 0.0)
    stations = []
    for name, lon, lat in _STATIONS:
        h1 = round(min(60.0, max(0.0, rng.gauss(hours[-1] + 1.5, 2.5))), 1)
        acc = round(sum(hours) + rng.uniform(-8, 8), 1)
        stations.append({"name": name, "lon": lon, "lat": lat,
                         "hour_rain_mm": max(0.0, h1), "rain24h_mm": max(0.0, acc)})
    last1 = hours[-1]
    trend = [{"hour": (datetime.datetime.now() - datetime.timedelta(hours=23 - i)).strftime("%H:%M"),
              "mm": round(v, 1)} for i, v in enumerate(hours[-12:])]
    if last1 >= 16:
        level, advice = "暴雨", "注意城区积涝"
    elif last1 >= 8:
        level, advice = "大雨", "出行请避开易涝路段"
    elif last1 >= 2.5:
        level, advice = "中雨", "降雨持续, 关注预警"
    elif last1 >= 0.1:
        level, advice = "小雨", "无积涝风险"
    else:
        level, advice = "无雨", "天气平静"
    return {"updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "slot_minutes": 10, "city_hour_rain_mm": round(last1, 1),
            "city_rain24h_mm": round(sum(hours), 1),
            "intensity_level": level, "advice": advice,
            "stations": stations, "trend_12h": trend,
            "note": "演示数据: 每10分钟生成一次确定性雨情情景(模拟实时接入), 未连接真实气象接口。"}


# ==== 防汛智能问答(本地意图解析, 离线可用; 输出结构化数据供前端联动三维) ====
def _fmt_pop(n):
    return ("%.1f 万人" % (n / 10000)) if n >= 10000 else ("%d 人" % n)


@app.post("/api/assistant")
def assistant(payload: dict = Body(...)):
    """防汛问答: 规则解析(重现期/雨量模拟/易涝点/影响/损失/预警/疏散/原理), 全离线。"""
    q = str(payload.get("question", "")).strip()
    if not q:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    import re as _re

    def has(*words):
        return any(w in q for w in words)

    def scen(T):
        with open(os.path.join(ROOT, "flood_out", "scenarios.json"), encoding="utf-8") as f:
            ss = json.load(f)
        return next((s for s in ss if s["return_period_y"] == T), None)

    # 重现期问题
    m = _re.search(r"(\d{1,3})\s*年", q)
    T = int(m.group(1)) if m and (has("一遇", "重现期", "年一遇") or
                                  (has("淹", "雨") and int(m.group(1)) in (2, 5, 10, 50, 100))) else None
    if T and has("避难", "疏散", "逃生"):
        ev = _evacuation_core(T)
        rts = (ev or {}).get("routes", [])[:3]
        if rts:
            ans = "%d年一遇情景下的疏散建议: " % T + \
                  "; ".join("从最大易涝点(%.2fkm²)沿规划路径向「%s」疏散(约%d米)"
                            % (r["from"]["area_km2"], r["to"]["name"], r["distance_m"]) for r in rts) + \
                  "。已在三维场景中绘制路径。"
        else:
            ans = "%d年一遇情景的疏散路径生成失败或当前无主要易涝点。" % T
        return {"answer": ans, "data": ev or {}, "action": {"type": "evacuation", "return_period": T}}

    # 自定义雨量
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", q)
    if m and has("雨", "淹", "mm", "毫米", "暴雨"):
        rain = float(m.group(1))
        if not (10 <= rain <= 2000):
            return {"answer": "请提供 10–2000 mm 范围的 24h 雨量, 例如: 降雨 300 毫米会怎么样?"}
        d = _online_sim_core(rain, 0.5)
        z0, _ = _get_dtm()
        lv = _compute_warning(z0, _bathtub(z0, rain / 1000 * 0.5)[1], "%gmm模拟" % rain)
        ans = ("模拟 %.0f mm/24h(径流系数C=0.5): 反演水位 %.2f m, 淹没 %.2f km², "
               "最大水深 %.2f m; 受影响建筑 %s 栋, 约 %s, 估算直接损失约 %.0f 万元。城市预警等级: %s。"
               % (rain, d["water_level_m"], d["flooded_area_km2"], d["max_depth_m"],
                  d["impact"]["affected_buildings"], _fmt_pop(d["impact"]["affected_population"]),
                  d["impact"]["estimated_loss_wan"], lv["city_level"]))
        return {"answer": ans, "data": d,
                "action": {"type": "online_sim", "rain_mm": rain, "c": 0.5}}

    if T:
        s = scen(T)
        if not s:
            return {"answer": "目前有 2/5/10/50/100 年一遇 5 档情景, 可以问我其中一档。"}
        dt = _load_depth_tif(T)
        im = impact_stats(dt, _get_dtm()[1]) if dt is not None else {}
        ans = ("%d年一遇(24h设计雨量 %s mm): 反演水位 %.2f m, 淹没 %.2f km²(占研究区约 %.0f%%), "
               "平均/最大水深 %.2f/%.2f m; 受影响建筑 %s/%s 栋、约 %s, 估算直接经济损失约 %.0f 万元。"
               % (T, s["rain_mm"], s["water_level_m"], s["flooded_area_km2"],
                  100.0 * s["flooded_area_km2"] / (4.0 * 4.44), s["mean_depth_m"], s["max_depth_m"],
                  im.get("affected_buildings", "--"), im.get("buildings_total", "--"),
                  _fmt_pop(im.get("affected_population", 0)), im.get("estimated_loss_wan", 0)))
        return {"answer": ans, "data": s, "action": {"type": "scenario", "return_period": T}}

    if has("易涝", "哪里", "积水点", "内涝点"):
        d100 = _load_depth_tif(100)
        z0, tr0 = _get_dtm()
        hs = hotspot_stats((d100 > DEPTH_THRESH) & (z0 > 0), d100, tr0, 3) if d100 is not None else []
        if not hs:
            return {"answer": "当前数据不足以解析易涝点。"}
        ans = "100年一遇情景下最易涝的 3 处: " + \
              "; ".join("%d) 面积 %.2f km²、最深 %.2f m(%.5f, %.5f)" %
                        (i + 1, h["area_km2"], h["max_depth_m"], h["lon"], h["lat"])
                        for i, h in enumerate(hs)) + "。已定位最严重一处。"
        return {"answer": ans, "data": {"hotspots": hs},
                "action": {"type": "hotspot", "bbox": hs[0]["bbox"], "lon": hs[0]["lon"], "lat": hs[0]["lat"]}}

    if has("影响", "多少栋", "建筑", "人口", "多少人"):
        im = impact_stats(_load_depth_tif(100), _get_dtm()[1])
        ans = ("100年一遇情景: 受影响建筑 %s/%s 栋, 受影响人口约 %s(%s口径), 淹没陆域 %.2f km²; "
               "分类型损失(万元): %s。"
               % (im["affected_buildings"], im["buildings_total"], _fmt_pop(im["affected_population"]),
                  "格网" if im["pop_source"] == "worldpop" else "密度估算", im["flooded_land_km2"],
                  im["loss_by_type_wan"]))
        return {"answer": ans, "data": im}

    if has("损失", "经济损失", "多少钱", "赔偿"):
        im = impact_stats(_load_depth_tif(100), _get_dtm()[1])
        ans = ("100年一遇情景建筑直接经济损失约 %.0f 万元(受影响 %s 栋)。分类型: %s。"
               "口径: 底面积×层数×重置单价×水深-损失率曲线(示例参数), 未计交通/管网等间接损失。"
               % (im["estimated_loss_wan"], im["affected_buildings"], im["loss_by_type_wan"]))
        return {"answer": ans, "data": im}

    if has("预警", "警戒", "警报"):
        d100 = _load_depth_tif(100)
        w = _compute_warning(_get_dtm()[0], d100, "100年一遇") if d100 is not None else {}
        zs = sorted([z for z in w.get("zones", []) if z["level"] != "无"],
                    key=lambda x: -_WARN_RANK[x["level"]])[:3]
        ans = "当前查看的 100年一遇情景城市级预警: %s。%s" % (w.get("city_level", "--"), w.get("advice", "")) + \
              ("高等级分区: " + "; ".join("区%d(%s, 占比%.0f%%)" % (z["zone"], z["level"], z["ratio_pct"])
                                      for z in zs) if zs else "各区均低于蓝色阈值。")
        return {"answer": ans, "data": w, "action": {"type": "warning"}}

    if has("避难", "疏散", "逃生", "撤离"):
        ev = _evacuation_core(100)
        rts = (ev or {}).get("routes", [])
        ans = ("已筛选 %d 处避难场所(公共设施/未受淹高层), 并为 %d 处主要易涝点规划了避开深水(≥0.3m)的疏散路径: "
               % (len((ev or {}).get("shelters", [])), len(rts))) + \
              "; ".join("→%s(%d米)" % (r["to"]["name"], r["distance_m"]) for r in rts[:3]) + \
              "。路径已在三维场景绘制。"
        return {"answer": ans, "data": ev or {}, "action": {"type": "evacuation", "return_period": 100}}

    if has("浴缸", "原理", "怎么算", "模型", "方法", "unet", "UNet"):
        return {"answer": "核心方法: ①情景模拟—P-III 设计雨量×径流系数→径流深, 浴缸法体积注水反演水位W, 水深=W−地形(仅陆域); "
                          "②真实事件—卫星影像经 UNet(GF-FloodNet 架构)提取水体掩膜, 由边界水位反演真实水面高程; "
                          "③在线模拟—输入任意24h雨量与径流系数C实时反演。全部结果在三维场景中联动展示。",
                "data": {}}

    if has("你好", "您好", "帮助", "你能", "是什么系统"):
        return {"answer": "我是防汛智能助手, 可以回答: 各重现期(2/5/10/50/100年)淹没情况、自定义雨量模拟"
                          "(如\"降雨300毫米会怎么样\")、易涝点位置、影响建筑与人口、经济损失、预警等级、疏散路径等。"
                          "试试点击下方快捷问题。", "data": {}}

    return {"answer": "我还没理解这个问题。你可以问: \"100年一遇淹多大?\"、\"降雨300毫米会怎么样?\"、"
                      "\"哪里最容易涝?\"、\"影响多少建筑?\"、\"现在什么预警?\"、\"怎么疏散?\"",
            "data": {}}


# ==== 公众报汛(移动端 H5: 上报积水点, 管理员核实) ====
_REPORT_DIR = os.path.join(ROOT, "reports")


def _reports_file():
    os.makedirs(_REPORT_DIR, exist_ok=True)
    return os.path.join(_REPORT_DIR, "reports.json")


def _load_reports():
    p = _reports_file()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_reports(lst):
    with open(_reports_file(), "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=1)


@app.post("/api/report")
def report_create(payload: dict = Body(...)):
    try:
        lst = _load_reports()
        rid = "r%d%03d" % (int(time.time() * 1000) % 10**11, len(lst) % 1000)
        item = {"id": rid,
                "lon": float(payload["lon"]) if payload.get("lon") is not None else None,
                "lat": float(payload["lat"]) if payload.get("lat") is not None else None,
                "location_text": str(payload.get("location_text", ""))[:120],
                "depth_est": str(payload.get("depth_est", ""))[:20],
                "desc": str(payload.get("desc", ""))[:300],
                "status": "待核实",
                "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        img = payload.get("image") or ""
        if isinstance(img, str) and img.startswith("data:image") and "," in img and len(img) < 3_500_000:
            try:
                raw = io.BytesIO(base64.b64decode(img.split(",", 1)[1]))
                ext = ".jpg" if "jpeg" in img[:32] else ".png"
                fn = "img_%s%s" % (rid, ext)
                Image.open(raw).convert("RGB").save(os.path.join(_REPORT_DIR, fn))
                item["image"] = fn
            except Exception:
                pass
        lst.insert(0, item)
        _save_reports(lst[:200])
        return {"ok": True, "id": rid, "item": item}
    except Exception as e:
        return JSONResponse({"error": "上报失败: %s" % e}, status_code=400)


@app.get("/api/report")
def report_list(limit: int = Query(50, ge=1, le=200)):
    lst = _load_reports()[:limit]
    for it in lst:
        if it.get("image"):
            it["image_url"] = "/reports/" + it["image"]
    return {"reports": lst, "total": len(lst)}


@app.post("/api/report/{rid}/status")
def report_status(rid: str, payload: dict = Body(...), token: str = Query(None)):
    if not _require_admin(token):
        return JSONResponse({"error": "需要管理员登录"}, status_code=401)
    st = str(payload.get("status", ""))
    if st not in ("待核实", "已核实", "已处理", "误报"):
        return JSONResponse({"error": "非法状态"}, status_code=400)
    lst = _load_reports()
    for it in lst:
        if it["id"] == rid:
            it["status"] = st
            _save_reports(lst)
            return {"ok": True, "item": it}
    return JSONResponse({"error": "未找到该上报"}, status_code=404)


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
