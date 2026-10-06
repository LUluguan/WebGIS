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
import io, json, math, os, re, time, uuid, random, datetime, base64
import threading
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
from fastapi import FastAPI, UploadFile, File, Query, Body, HTTPException, Depends, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
import logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
_log = logging.getLogger("flood")


class _TokenRedactFilter(logging.Filter):
    """uvicorn 访问日志会把 query 里的 token 原文落盘(2026-10 审计 SEC-05):
    进入 stdout/容器日志/采集器, 也被浏览器历史与 Referer 带走。统一替换为 ***。"""
    _PAT = re.compile(r"([?&]token=)[^&\s]+")

    def filter(self, record):
        msg = record.getMessage()
        if "token=" in msg:
            record.msg = self._PAT.sub(r"\1***", msg)
            record.args = ()
        return True


logging.getLogger("uvicorn.access").addFilter(_TokenRedactFilter())

ROOT = os.path.dirname(os.path.abspath(__file__))
# 读取 .env(可选): 提供 FLOOD_DB_* / GEOSCENE_* 配置; 未配置口令时数据库连接失败, 接口自动回退本地数据
if load_dotenv:
    load_dotenv(os.path.join(ROOT, ".env"))
DB = dict(
    host=os.environ.get("FLOOD_DB_HOST", "localhost"),
    port=int(os.environ.get("FLOOD_DB_PORT", "5432")),
    dbname=os.environ.get("FLOOD_DB_NAME", "flood_analysis"),
    user=os.environ.get("FLOOD_DB_USER", "postgres"),
    password=os.environ.get("FLOOD_DB_PASSWORD"),
)
from pipeline_config import (DEPTH_THRESH, RUNOFF_COEF, STUDY_WS,
                            DEPTH_BINS, CELL_LAT, M_PER_DEG_LON_EQUATOR,
                            M_PER_DEG_LAT, cell_area_m2)  # 全仓唯一权威常量
from store import (_REPORT_DIR, _load_reports, _save_reports,
                    _report_lock, _subscribers_lock, SUBSCRIBER_CAP,
                    _rate_ok, _client_ip, _clean_text,
                    _load_subscribers, _save_subscribers,
                    _hash_pw, _load_users)
from flood_render import colorize   # 水深色带唯一实现(app/导出脚本共用, ARC-01)
from rasterio.features import shapes as _rio_shapes, rasterize as _rio_rasterize
import heapq
from unet_apply import predict_mask
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

# 交互式 API 文档默认关闭(不向访客暴露接口面); 设 FLOOD_DOCS=1 按需开启
_DOCS = os.environ.get("FLOOD_DOCS", "").strip() == "1"
_state = {"db_fallbacks": 0, "scenarios_source": "file"}

app = FastAPI(
    title="广东降雨洪涝 WebGIS 服务层", version="1.3",
    docs_url="/docs" if _DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _DOCS else None,
    # 关闭斜杠自动重定向: 否则 "//x" 会被规范化链路兜回欢迎页(200 HTML),
    # 让 getJSON 的 r.ok 检查通过却解析出 HTML, 掩盖真实 404; 也防 "//key" 类探测借道
    redirect_slashes=False)
# 响应 gzip 压缩: Cesium/ECharts 等静态文本体积降 70–85%(首屏 4.7MB → ~1MB)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def _slow_request_log(request, call_next):
    """慢请求观测: >300ms 打 warning, 现场卡顿时可立刻定位是哪一端。"""
    t0 = time.perf_counter()
    resp = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    if ms > 300:
        _log.warning("SLOW %s %s -> %d (%.0f ms)", request.method, request.url.path,
                     resp.status_code, ms)
    return resp


@app.exception_handler(HTTPException)
async def _http_exc_as_error(request, exc: HTTPException):
    """HTTPException 统一渲染为前端约定的 {"error": ...} 形状。"""
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_exc_as_error(request, exc: RequestValidationError):
    """422 校验错误同样渲染为 {"error": ...}(修聊天/前端读 d.error 得 undefined)。"""
    brief = "; ".join("%s %s" % (".".join(map(str, e.get("loc", [])[1:])), e.get("msg", ""))
                      for e in exc.errors()[:3])
    return JSONResponse({"error": "参数校验失败: " + brief}, status_code=422)


def _depth_or_404(return_period: int = Query(100, ge=2, le=100)):
    """公共依赖: 载入重现期水深栅格, 缺档统一 404(替代 6 处三行重复前奏)。"""
    d = _load_depth_tif(return_period)
    if d is None:
        raise HTTPException(status_code=404, detail="无 %d 年水深栅格" % return_period)
    return d


def get_conn():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 未安装")
    return psycopg2.connect(**DB)


# colorize 已下沉 flood_render.py(共享唯一实现, 顶部 import 使用)


# ==== 共享缓存: 简单"查→算→存"统一走 _Memo(一把锁防并发首击 dogpile, FIFO 上限淘汰)。
# 保留手写的特例(语义特殊, 勿强行统一):
#   _warn_cache    published_at 需每请求刷新
#   _monthly_cache 缺失回退 + 存在性复检(测试打桩依赖)
#   _theme_cache   自定义标题不入缓存
#   _evac_cache    结果聚合对象大, 且含"缺栅格→None"分支
class _Memo:
    def __init__(self, limit=None):
        self._data = {}
        self._order = []
        self._lock = threading.Lock()
        self._limit = limit
        self._hits = 0
        self._misses = 0

    def stats(self):
        """(命中, 未命中) — 供 /api/health 观测。"""
        return (self._hits, self._misses)

    def get_or_compute(self, key, fn):
        with self._lock:
            if key in self._data:
                self._hits += 1
                return self._data[key]
            self._misses += 1
        val = fn()
        with self._lock:
            if key not in self._data:
                self._data[key] = val
                self._order.append(key)
                if self._limit and len(self._order) > self._limit:
                    self._data.pop(self._order.pop(0), None)
        return val

_dtm_m = _Memo()
_png_m = _Memo()
_hist_m = _Memo()
_zone_m = _Memo()
_hotspot_m = _Memo()
_bld_m = _Memo()
_pop_m = _Memo()
_unc_m = _Memo()
_evac_m = _Memo()


def _get_dtm():
    """缓存读去建筑 DTM(study_dtm.tif), 与重现期场景同一份地形。返回 (z, transform)。"""
    def _load():
        p = os.path.join(ROOT, "dem", "study_dtm.tif")
        if not os.path.exists(p):
            raise RuntimeError("study_dtm.tif 缺失, 请先运行 pipeline/bathtub_flood.py")
        with rasterio.open(p) as src:
            z = src.read(1).astype("float32")
            transform = src.transform
        z[np.isnan(z)] = 0.0
        return z, transform
    return _dtm_m.get_or_compute("dtm", _load)


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


# _cell_area_m2 已上提 pipeline_config.cell_area_m2(面积换算唯一权威, ARC-01)


def _zone_ratios(z, depth, grid=3):
    """grid×grid 分区陆地淹没占比(仅陆地, 排除河道), 与 /api/zone_flood 同算法。
    口径与 _zone_depth_stats/预警一致: depth > DEPTH_THRESH(旧版 >0 会把 0-5cm 薄水计入,
    与预警条差 0.1–0.6pp 足以跨级)。"""
    land = z > 0
    flood = (depth > DEPTH_THRESH) & land
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
    return abs(a) / 2 * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)) * M_PER_DEG_LAT


def _get_buildings():
    """缓存建筑(珠江新城 290 栋): 质心经纬度 + 名称 + 高度 + 底面积 + 类型。"""
    def _load():
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
        return out
    return _bld_m.get_or_compute("bld", _load)


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
    """缓存人口格网(pipeline/fetch_pop.py 预生成, 已重采样到 DTM 网格); 缺失返回 None。"""
    def _load():
        p = os.path.join(ROOT, "dem", "study_pop.tif")
        if os.path.exists(p):
            try:
                with rasterio.open(p) as src:
                    return src.read(1).astype("float32")
            except Exception as e:
                _log.warning("study_pop.tif 读取失败, 人口退化为密度估算: %s", e)
        return None
    return _pop_m.get_or_compute("pop", _load)


def _sample_grid(arr, transform, lon, lat):
    """在网格 arr 上取 (lon,lat) 处的值; 越界返回 None。
    用 floor 而非 int()(int 向零截断, 负坐标 -0.5 会回绕成 0 造成假命中)。"""
    try:
        inv = ~transform
        col, row = inv * (lon, lat)
    except Exception:
        return None
    r, c = int(math.floor(row)), int(math.floor(col))
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]:
        return float(arr[r, c])
    return None


def impact_stats(depth, transform):
    """受影响建筑(质心处水深>0.05m)与受影响人口。

    人口口径: 优先用 WorldPop 100m 人口格网(pipeline/fetch_pop.py 产出 dem/study_pop.tif,
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
    flood_km2 = float(flood_m.sum()) * cell_area_m2(transform) / 1e6
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
    """多边形面积(km²) = _ring_area_m2 / 1e6(原逐字符重复实现已合一)。"""
    return _ring_area_m2(ring, lat) / 1e6


def hotspot_stats(flood_mask, depth, transform, top=8):
    """淹没斑块(已要求 flood_mask 为陆地淹没)按面积排序的 Top-N:
    面积 / 最大水深 / 平均水深 / 中心点与外包围盒(供前端定位)。
    优化: 先按多边形面积排序, 只对 top-N 栅格化统计(原实现全斑块的栅格化是大头)。"""
    if not flood_mask.any():
        return []
    geoms = [(g, v) for g, v in _rio_shapes(flood_mask.astype("uint8"), mask=flood_mask, transform=transform)
             if v == 1]
    # 先算多边形面积并降序, 只栅格化前 top*3 个(留出dv过滤后的余量)
    def poly_km2(item):
        g = item[0]
        ring = g["coordinates"][0]
        lats = [pt[1] for pt in ring]
        return _ring_area_km2(ring, sum(lats) / len(lats))
    geoms.sort(key=poly_km2, reverse=True)
    items = []
    for g, v in geoms[:max(top * 3, 12)]:
        ring = g["coordinates"][0]
        lons = [pt[0] for pt in ring]
        lats = [pt[1] for pt in ring]
        lat_c = sum(lats) / len(lats)
        # 栅格统计该斑块水深
        m = _rio_rasterize([(g, 1)], out_shape=depth.shape, transform=transform,
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
    items.sort(key=lambda x: -x["area_km2"])
    return items[:top]


# ==== 真实事件(多事件注册表) ====
def _event_registry():
    reg_p = os.path.join(ROOT, "realevent_out", "events.json")
    if os.path.exists(reg_p):
        try:
            with open(reg_p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _log.warning("事件注册表 realevent_out/events.json 解析失败, 真实事件页将为空: %s", e)
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
def _artifact(rel):
    return os.path.exists(os.path.join(ROOT, rel))


def _db_reachable():
    if psycopg2 is None:
        return False
    try:
        with get_conn() as c:
            c.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


@app.get("/api/health", tags=["系统"])
def health():
    """运行状态: 版本/DB 可达性/数据源策略/关键产物存在性/缓存观测/降级计数。
    DB 降级不再无声——这里能看出 scenarios 走的是库还是文件。"""
    memos = {"dtm": _dtm_m, "png": _png_m, "hist": _hist_m, "zone": _zone_m,
             "hotspot": _hotspot_m, "bld": _bld_m, "pop": _pop_m, "unc": _unc_m,
             "evac": _evac_m}
    hits = sum(m.stats()[0] for m in memos.values())
    misses = sum(m.stats()[1] for m in memos.values())
    return {
        "status": "ok", "service": "flood-webgis", "version": app.version,
        "db_reachable": _db_reachable(), "db_first": _db_first(),
        "scenarios_source": _state.get("scenarios_source", "file"),
        "db_fallbacks": _state.get("db_fallbacks", 0),
        "store_quarantines": _state.get("store_quarantines", 0),
        "artifacts": {
            "design_storm_24h": _artifact(os.path.join("flood_out", "design_storm_24h.json")),
            "flood_tiles": _artifact(os.path.join("flood_out", "flood_tiles", "meta.json")),
            "unet_water_pt": _artifact(os.path.join("unet_out", "unet_water.pt")),
            "study_dtm": _artifact(os.path.join("dem", "study_dtm.tif")),
            "scenarios_json": _artifact(os.path.join("flood_out", "scenarios.json")),
            "config_local_js": _artifact(os.path.join("web", "config.local.js")),
        },
        "cache": {"hits": hits, "misses": misses,
                  "hit_rate": round(hits / max(hits + misses, 1), 3),
                  "sizes": {k: len(m._data) for k, m in memos.items()}},
    }


_uncertainty_cache = None


def _get_uncertainty():
    """缓存读 flood_out/uncertainty.json(tools/uncertainty_bands.py 生成); 缺失返回 None。"""
    def _load():
        p = os.path.join(ROOT, "flood_out", "uncertainty.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return None
    return _unc_m.get_or_compute("unc", _load)


def _db_first():
    """数据源策略: 默认本地优先(与重算管线/栅格端点同源, 避免 DB 旧快照与新结果自相矛盾);
    .env 设 FLOOD_DB_FIRST=1 时恢复"库优先、失败回退本地"(PostGIS 演示模式)。"""
    return os.environ.get("FLOOD_DB_FIRST", "").strip() == "1"


@app.get("/api/scenarios", tags=["情景与栅格"])
def scenarios():
    source = "local"
    base = None
    if _db_first():
        try:
            with get_conn() as c, c.cursor() as cur:
                cur.execute("""SELECT return_period_y, rain_mm, runoff_depth_m, water_level_m,
                                      max_depth_m, mean_depth_m, flooded_area_km2, flooded_cells, river_cells
                               FROM flood_scenarios ORDER BY return_period_y""")
                cols = [d[0] for d in cur.description]
                base = [dict(zip(cols, r)) for r in cur.fetchall()]
                source = "postgis"
        except Exception as e:
            _log.warning("PostGIS flood_scenarios 查询失败, 回退本地文件: %s", e)
            _state["db_fallbacks"] += 1
            base = None
    if base is None:
        # 本地文件(与 pipeline/bathtub_flood.py 重算产物同源)
        p = os.path.join(ROOT, "flood_out", "scenarios.json")
        if not os.path.exists(p):
            return JSONResponse({"error": "scenarios.json 缺失, 请先运行 pipeline/bathtub_flood.py"}, status_code=404)
        with open(p, encoding="utf-8") as f:
            base = json.load(f)
    # 合并不确定性带(降雨 ±20% 敏感性, tools/uncertainty_bands.py 预生成)
    unc = _get_uncertainty()
    if unc:
        for s in base:
            u = unc.get("scenarios", {}).get(str(s.get("return_period_y")))
            if not u:
                continue
            s["W_low_m"], s["W_high_m"] = u["W"]["low"], u["W"]["high"]
            s["area_low_km2"], s["area_high_km2"] = u["area_km2"]["low"], u["area_km2"]["high"]
            s["uncertainty_note"] = unc.get("note", "")
    for s in base:
        s["source"] = source
    _state["scenarios_source"] = source
    return base


@app.get("/api/flood_extent", tags=["情景与栅格"])
def flood_extent(return_period: int = Query(100, ge=2, le=100)):
    if _db_first():
        try:
            with get_conn() as c, c.cursor() as cur:
                cur.execute("SELECT ST_AsGeoJSON(geom) FROM flood_extent WHERE return_period_y=%s",
                            (return_period,))
                feats = [{"type": "Feature", "geometry": json.loads(r[0]), "properties": {}}
                         for r in cur.fetchall()]
            if feats:
                _state["scenarios_source"] = "postgis"
                return {"type": "FeatureCollection", "features": feats, "source": "postgis"}
        except Exception as e:
            _log.warning("PostGIS flood_extent(T=%s) 查询失败, 回退本地文件: %s", return_period, e)
            _state["db_fallbacks"] += 1
    # 本地 geojson(与 pipeline/bathtub_flood.py 重算产物同源; 只有 2/5/10/50/100 五档, 其余重现期 404)
    p = os.path.join(ROOT, "flood_out", "flood_extent_%dy.geojson" % return_period)
    if not os.path.exists(p):
        return JSONResponse({"error": "无 %d 年一遇的淹没范围数据(可用: 2/5/10/50/100)" % return_period},
                            status_code=404)
    with open(p, encoding="utf-8") as f:
        gj = json.load(f)
    gj["source"] = "local"
    _state["scenarios_source"] = "local"
    return gj


@app.get("/api/flood_depth_png", tags=["情景与栅格"])
def flood_depth_png(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100)):
    def _render():
        z, _ = _get_dtm()
        buf = io.BytesIO()
        colorize(d, land=z > 0).save(buf, format="PNG")   # 仅陆域着色, 排除常年河道
        return buf.getvalue()
    return Response(content=_png_m.get_or_compute(return_period, _render), media_type="image/png")


_monthly_cache = None


@app.get("/api/monthly_rain", tags=["情景与栅格"])
def monthly_rain():
    """研究区 5 年逐月降雨(mm), 供数据大屏「降雨态势」图。precip_tif 未随仓库分发时优雅回退。
    结果进程级缓存(数据只读); 命中时仍轻量核验文件存在(环境变化/测试打桩需能触发回退)。"""
    global _monthly_cache
    paths = [os.path.join(ROOT, "precip_tif", "precip_%d.tif" % yr) for yr in (2021, 2022, 2023, 2024, 2025)]
    if _monthly_cache is not None and all(os.path.exists(p) for p in paths):
        return _monthly_cache
    years = [2021, 2022, 2023, 2024, 2025]  # 降雨年份(与 RETURNS 重现期档位无关)
    out = {}
    for yr, p in zip(years, paths):
        if not os.path.exists(p):
            _monthly_cache = None
            return {"years": [], "months": [], "monthly_rain": {},
                    "note": "precip_tif 数据未分发, 降雨态势不可用"}
        with rasterio.open(p) as src:
            w = from_bounds(*STUDY_WS, src.transform).round_offsets().round_lengths()
            d = src.read(window=w).astype("float32")
        d[d == -32768] = np.nan
        out[yr] = [round(float(np.nanmedian(d[b])) * 0.1, 1) for b in range(12)]
    _monthly_cache = {"years": years, "months": list(range(1, 13)), "monthly_rain": out}
    return _monthly_cache


@app.get("/api/depth_hist", tags=["情景与栅格"])
def depth_hist(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100)):
    """水深分布直方图 + 预警等级统计(按重现期, 仅陆地淹没, 排除河道)。"""
    def _compute():
        # 排除河道(z<=0): 只统计陆地淹没水深, 与淹没范围一致, 避免河道深水扭曲分布
        z, _ = _get_dtm()
        dd = d[(d > DEPTH_THRESH) & (z > 0)]
        bins = DEPTH_BINS
        labels = ["0-0.5m", "0.5-1m", "1-2m", "2-3m", "3-5m", ">5m"]
        counts = [int(((dd > lo) & (dd <= hi)).sum()) for lo, hi in bins]
        warn = {"蓝": int((dd <= 0.5).sum()), "黄": int(((dd > 0.5) & (dd <= 1)).sum()),
                "橙": int(((dd > 1) & (dd <= 2)).sum()), "红": int((dd > 2).sum())}
        return {"return_period": return_period, "labels": labels, "counts": counts, "warn": warn}
    return _hist_m.get_or_compute(return_period, _compute)


@app.get("/api/geoscene", tags=["GeoScene"])
def geoscene():
    """GeoScene/ArcGIS Online 服务配置。未配置时 enabled=false, 前端回退本地数据。"""
    return {
        "enabled": GEOSCENE_ENABLED,
        "extent_url": GEOSCENE_EXTENT_URL,
        "depth_url": GEOSCENE_DEPTH_URL,
        "note": "未配置时前端自动回退读取本地 flood_out/ 数据",
    }


@app.get("/api/zone_flood", tags=["影响分析"])
def zone_flood(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100),
               grid: int = Query(3, ge=2, le=4)):
    """grid×grid 网格分区淹没占比: "陆地淹没(depth>0且z>0)占陆地之比"。
    排除珠江河道(常年水体, 非淹没), 与淹没范围/水深分布口径一致。"""
    def _compute():
        z, _ = _get_dtm()
        return {"return_period": return_period, "grid": grid, "n": grid * grid,
                "zones": _zone_ratios(z, d, grid)}
    return _zone_m.get_or_compute((return_period, grid), _compute)


@app.get("/api/impact", tags=["影响分析"])
def impact(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100)):
    """淹没影响统计: 受影响建筑(质心处水深>0.05m, Top5 列出最深)与受影响人口(WorldPop)。"""
    try:
        _, transform = _get_dtm()
        return {"return_period": return_period, **impact_stats(d, transform)}
    except RuntimeError as e:
        # 仅数据缺失类配置错误回 404(消息为仓库自有文案, 非内部异常原文)
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception:
        _log.exception("%s 内部错误(真实缺陷, 按 500 上报排查)", "impact")
        return JSONResponse({"error": "服务内部错误, 请联系维护者查看日志"}, status_code=500)


@app.get("/api/hotspots", tags=["影响分析"])
def hotspots(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100),
             top: int = Query(8, ge=1, le=20)):
    """易涝点 Top-N: 按淹没斑块面积排序(仅陆地淹没), 含最大/平均水深与定位 bbox。"""
    def _compute():
        z, transform = _get_dtm()
        flood = (d > DEPTH_THRESH) & (z > 0)
        return {"return_period": return_period, "hotspots": hotspot_stats(flood, d, transform, top)}
    return _hotspot_m.get_or_compute((return_period, top), _compute)


@app.get("/api/critical_assets", tags=["影响分析"])
def critical_assets(d=Depends(_depth_or_404), return_period: int = Query(100, ge=2, le=100)):
    """关键设施影响清单(参考 Esri Flood Impact Analysis):
    公共设施(学校/医院/文体/政务等)在质心处的水深与受淹状态, 按水深降序。
    水深口径与 /api/impact 一致: 仅陆域淹没(河道常年水体不计)。"""
    z, transform = _get_dtm()
    d_land = np.where(z > 0, d, 0.0)
    items = []
    for b in _get_buildings():
        if b["btype"] != "公共":
            continue
        dv = _sample_grid(d_land, transform, b["lon"], b["lat"])
        depth = round(dv, 2) if (dv is not None and dv > DEPTH_THRESH) else 0.0
        zg = _sample_grid(z, transform, b["lon"], b["lat"])
        items.append({"name": b["name"] or "公共设施", "lon": b["lon"], "lat": b["lat"],
                      "height_m": b["height_m"], "btype": b["btype"],
                      "ground_m": round(zg, 2) if zg is not None else None,
                      "depth_m": depth, "flooded": depth > 0,
                      "loss_wan": round(_building_loss(b, dv), 1) if (dv is not None and dv > 0) else 0.0})
    # 排序: 受淹按水深降序; 未受淹按地面高程升序(最接近水线 = 最脆弱)
    items.sort(key=lambda x: (-x["depth_m"],
                              x["ground_m"] if x["ground_m"] is not None else 999.0))
    note = ""
    if items and not any(x["flooded"] for x in items):
        note = ("本情景公共设施均高于水面(珠江新城商务区公共设施基底较高); "
                "清单按地面高程升序列出最脆弱设施, 供竖向避险与转移参考。")
    return {"return_period": return_period,
            "total_public": len(items),
            "flooded": sum(1 for x in items if x["flooded"]),
            "note": note,
            "assets": items}


def _online_sim_core(rain_mm, c, top=5):
    """在线模拟核心逻辑(供端点与问答复用; 参数须为普通数值)。"""
    z, transform = _get_dtm()
    q = rain_mm / 1000.0 * c
    W, depth = _bathtub(z, q)
    land = z > 0
    flooded = (depth > DEPTH_THRESH) & land
    feats = []
    if flooded.any():
        for g, v in _rio_shapes(flooded.astype("uint8"), mask=flooded, transform=transform):
            if v == 1:
                feats.append({"type": "Feature", "geometry": g, "properties": {}})
    area = cell_area_m2(transform)
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


@app.get("/api/online_sim", tags=["在线模拟"])
def online_sim(rain_mm: float = Query(..., gt=0, le=2000),
               c: float = Query(RUNOFF_COEF, ge=0.05, le=0.95),
               top: int = Query(5, ge=1, le=20)):
    """在线模拟: 输入 24h 雨量(mm)与综合径流系数 c(0.05-0.95, 海绵城市改造可降低 c)
    → 浴缸法实时反演水位/淹没范围/分区占比/影响统计/易涝点。默认 c 与重现期场景同口径。"""
    try:
        return _online_sim_core(rain_mm, c, top)
    except RuntimeError as e:
        # 仅数据缺失类配置错误回 404(消息为仓库自有文案, 非内部异常原文)
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception:
        _log.exception("%s 内部错误(真实缺陷, 按 500 上报排查)", "online_sim")
        return JSONResponse({"error": "服务内部错误, 请联系维护者查看日志"}, status_code=500)


@app.get("/api/realevent", tags=["真实事件"])
def realevent_list():
    """真实事件注册表(多事件): [{id, name}] + 默认事件。"""
    reg = _event_registry()
    return {"default": reg["default"],
            "events": [{"id": e["id"], "name": e["name"]} for e in reg["events"]]}


@app.get("/api/realevent/{event_id}", tags=["真实事件"])
def realevent_meta(event_id: str):
    """真实事件元数据(UNet 反演水深)+ 事件档案(摘要/历时/峰值/数据源/验证说明)。
    档案字段来自 realevent_events.json 的 archive 节点(参考 USGS Flood Event Viewer 事件档案模式)。"""
    d = _event_dir(event_id)
    if d is None:
        return JSONResponse({"error": "未知事件 %s" % event_id}, status_code=404)
    p = os.path.join(d, "realevent.json")
    if not os.path.exists(p):
        return JSONResponse({"error": "事件数据未生成, 请先运行 pipeline/realevent_beijiang.py --event %s" % event_id},
                            status_code=404)
    with open(p, encoding="utf-8") as f:
        meta = json.load(f)
    meta["dir"] = os.path.relpath(d, ROOT).replace("\\", "/")
    # 事件档案: 从管线配置 realevent_events.json 的 archive 节点合并输出
    evcfg_p = os.path.join(ROOT, "realevent_events.json")
    if os.path.exists(evcfg_p):
        try:
            with open(evcfg_p, encoding="utf-8") as f:
                evcfg = json.load(f)
            if isinstance(evcfg.get(event_id, {}).get("archive"), dict):
                meta["archive"] = evcfg[event_id]["archive"]
        except Exception as e:
            _log.warning("事件档案(archive)合并失败 %s: %s", event_id, e)
    return meta


@app.get("/api/realevent_extent", tags=["真实事件"])
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
    from rasterio.transform import from_origin
    im = np.asarray(Image.open(mask_p))
    m = im > 128
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    west, south, east, north = meta["bbox"]
    rows, cols = m.shape
    tr = from_origin(west, north, (east - west) / cols, (north - south) / rows)
    feats = []
    for g, v in _rio_shapes(m.astype("uint8"), mask=m, transform=tr):
        if v == 1:
            feats.append({"type": "Feature", "geometry": g, "properties": {}})
    return {"type": "FeatureCollection", "features": feats}


MAX_PREDICT_BYTES = 64 * 1024 * 1024


def _read_capped(stream, cap=MAX_PREDICT_BYTES):
    """流式截断读取: 边读边弃, 峰值内存有界。
    (SEC-03: 旧版先整份 read 进内存再判长, 2GB 恶意请求即可打爆进程)"""
    buf = bytearray()
    while True:
        chunk = stream.read(1 << 20)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            raise HTTPException(status_code=413, detail="文件超过 64MB 上限")
    return bytes(buf)


@app.post("/api/predict", tags=["UNet推理"])
def predict(request: Request, file: UploadFile = File(...)):
    """UNet 水体提取: 上传 5 波段 GeoTIFF -> 水体二值掩膜 PNG。
    归一化用 unet_apply.predict_mask 的 auto 策略(与真实事件管线同一路径)。
    同步 def(FastAPI 自动进线程池)不阻塞事件循环; unet_apply.load_model 进程级缓存模型。
    上限 64MB(流式截断); CPU 推理是全站最贵计算 → 5 次/分钟频控; 非 GeoTIFF 输入归 400。"""
    if not _rate_ok("predict:" + _client_ip(request), limit=5, window=60.0):
        return JSONResponse({"error": "请求过于频繁, 请稍后再试"}, status_code=429)
    ckpt = os.path.join(ROOT, "unet_out", "unet_water.pt")
    if not os.path.exists(ckpt):
        return JSONResponse({"error": "模型尚未训练完成"}, status_code=503)
    try:
        data = _read_capped(file.file)
        I = tifffile.imread(io.BytesIO(data)).astype(np.float32)
        if I.ndim == 3 and I.shape[0] == 5 and I.shape[2] != 5:
            I = I.transpose(1, 2, 0)          # (C,H,W) 波段前置 → (H,W,C)
        if I.ndim != 3 or I.shape[2] < 5:
            return JSONResponse({"error": "输入应为 5 波段影像 (H,W,5) 或 (5,H,W)"}, status_code=400)
        mask = predict_mask(I[..., :5], 128, ckpt_path=ckpt)
        buf = io.BytesIO()
        Image.fromarray(mask, "L").save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        _log.warning("UNet 推理失败(输入不支持或文件损坏): %s", e)
        return JSONResponse({"error": "推理失败: 输入不是受支持的 5 波段 GeoTIFF"}, status_code=400)


# ================= 复赛增强: 预警 / 疏散 / 专题图 / 问答 / 雨情 / 报汛 / 认证 =================

# ==== 用户认证(轻量: 文件用户表 + 内存 token, 管理员/公众两角色) ====
_tokens = {}   # token -> {username, role, ts}


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


def _token_from(request, query_token):
    """token 提取: 优先 Authorization: Bearer / x-auth-token 头, query 兼容。
    (SEC-05: query 里的 token 会进访问日志与浏览器历史; 头部优先, query 仅为兼容存量前端)"""
    auth = (request.headers.get("authorization") or "") if request else ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request is not None:
        h = request.headers.get("x-auth-token")
        if h:
            return h
    return query_token


@app.post("/api/auth/login")
def auth_login(request: Request, payload: dict = Body(...)):
    uname = str(payload.get("username", "")).strip()
    pw = str(payload.get("password", ""))
    # 爆破防护(SEC-02: 实测 60 次错口令无一 429): IP+用户名双键滑窗限流, 5 次/分钟
    if not _rate_ok("login:%s|%s" % (_client_ip(request), uname[:40]), limit=5, window=60.0):
        return JSONResponse({"error": "尝试过于频繁, 请 1 分钟后再试"}, status_code=429)
    for u in _load_users():
        if u["username"] == uname and u["hash"] == _hash_pw(pw, u["salt"]):
            tok = uuid.uuid4().hex
            _tokens[tok] = {"username": uname, "role": u["role"], "ts": time.time()}
            exp = (datetime.datetime.now() + datetime.timedelta(hours=12)).strftime("%Y-%m-%d %H:%M")
            return {"token": tok, "username": uname, "role": u["role"],
                    "expires_at": exp, "expires_in_hours": 12}
    return JSONResponse({"error": "用户名或密码错误"}, status_code=401)


@app.get("/api/auth/me")
def auth_me(request: Request, token: str = Query(None)):
    u = _current_user(_token_from(request, token))
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
    area_km2 = round(float(flooded.sum()) * cell_area_m2(_get_dtm()[1]) / 1e6, 3)
    city = _city_level_by_area(area_km2)
    style = _WARN_STYLE.get(city, {"color": "#9fb3cc", "advice": "正常状态, 保持关注"})
    return {
        "scenario": label, "city_level": city, "city_rank": _WARN_RANK[city],
        "color": style["color"], "advice": style["advice"],
        "flooded_area_km2": area_km2,
        "zones": zones,
        "message": "珠江新城片区分级预警: %s" % (city if city != "无" else "无预警"),
        "published_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": "城区: 淹没面积≥0.6/1.0/1.3/1.6km² → 蓝/黄/橙/红; 分区: 淹没占比≥3%/8%/14%/20%",
        "note": "预警等级由淹没模拟结果自动分级, 供演示。真实业务需衔接三防部门发布流程。",
    }


@app.get("/api/warning", tags=["预警发布"])
def warning(return_period: int = Query(100, ge=2, le=100)):
    key = ("T", return_period)
    if key in _warn_cache:
        w = dict(_warn_cache[key])
        w["published_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")   # 缓存命中也用新鲜发布时间
    else:
        d = _load_depth_tif(return_period)
        if d is None:
            return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
        w = _compute_warning(_get_dtm()[0], d, "%d年一遇" % return_period)
        _warn_cache[key] = w
        w = dict(w)
    # 订阅触达统计(参考 Flood Hub Email Subscriptions; 演示环境模拟"推送链路已通")
    if _WARN_RANK.get(w.get("city_level", "无"), 0) >= 2:
        w["notify"] = {"subscribers": len(_load_subscribers()),
                       "channel": "邮件(演示登记, 未真实发送)",
                       "note": "橙/红预警触发订阅触达; 正式业务接入短信/邮件网关"}
    return w


# ==== 避难场所 + 疏散路径(A* 避水寻路) ====
_evac_cache = {}
_SHELTER_KEYS = PUBLIC_KEYS


def _pick_shelters(blds, k=6):
    """避难场所: 未受淹建筑中优先公共设施(图书馆/体育/学校…), 不足补高层(竖向避难)。
    在候选中按"最远点采样"取 k 个, 保证空间分散。
    blds 必须是带 depth 字段的建筑副本(唯一调用点 _evacuation_core 已传; 缺 depth 字段视为未受淹)。"""
    dry_pub, dry_high = [], []
    for b in blds:
        if b.get("depth") is not None and b["depth"] > DEPTH_THRESH:
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
    def _compute():
        d = _load_depth_tif(return_period)
        if d is None:
            return None
        return _evacuation_build(return_period, max_routes, d)
    return _evac_m.get_or_compute((return_period, max_routes), _compute)


def _evacuation_build(return_period, max_routes, d):
    z, transform = _get_dtm()
    d_land = np.where(z > 0, d, 0.0)
    # 在副本上挂临时 depth 字段: _bld_cache 是全端点共享缓存, 40 线程并发下不可就地修改
    blds = [dict(b) for b in _get_buildings()]
    for b in blds:
        b["depth"] = _sample_grid(d_land, transform, b["lon"], b["lat"])
    shelters = _pick_shelters(blds, 6)
    if not shelters:
        return {"return_period": return_period, "shelters": [], "routes": [], "stranded": [],
                "walk_rule": "网格 A* 成本感知寻路",
                "note": "当前情景下无干燥公共/高层建筑可作避难场所(全域高淹没)。"}
    hs = hotspot_stats((d > DEPTH_THRESH) & (z > 0), d, transform, top=max_routes)
    # 网格化: ≥0.8m 不可通行(成人涉水极限); 0.5–0.8m 高风险×6, 0.15–0.5m 涉水×3
    blocked = (d >= 0.8) | (z <= 0)
    wade = np.where(d >= 0.5, 6.0, np.where(d > 0.15, 3.0, 1.0))
    lab, _sizes = _island_labels(blocked)
    inv = ~transform
    cell_m = cell_area_m2(transform) ** 0.5

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
    for b in sorted([b for b in blds
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
    # depth 字段只存在于副本 blds 上, 共享缓存 _get_buildings() 无需(也无法)被污染
    res = {"return_period": return_period,
           "shelters": shelters, "routes": routes, "stranded": stranded,
           "walk_rule": "网格 A* 成本感知寻路: 水深≥0.8m 不可通行(涉水极限), 0.5–0.8m 高风险(×6), 0.15–0.5m 涉水(×3)",
           "strategy": "同岛避难场所(步行优先) → 同岛干燥高层(竖向避险) → 孤岛标记待救援",
           "note": "避难场所由未受淹公共设施/高层建筑自动筛选(示例), 供应急决策演示。"}
    return res


@app.get("/api/evacuation", tags=["疏散分析"])
def evacuation(return_period: int = Query(100, ge=2, le=100),
               max_routes: int = Query(4, ge=1, le=8)):
    """避难场所与疏散路径: 主要易涝点 → 最近可达避难场所, A* 网格寻路(深水阻断/浅水涉水)。"""
    try:
        res = _evacuation_core(return_period, max_routes)
    except RuntimeError as e:
        # 仅数据缺失类配置错误回 404(消息为仓库自有文案, 非内部异常原文)
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception:
        _log.exception("%s 内部错误(真实缺陷, 按 500 上报排查)", "evacuation")
        return JSONResponse({"error": "服务内部错误, 请联系维护者查看日志"}, status_code=500)
    if res is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    return res


# ==== 洪涝风险专题图 PNG(纯 PIL 出图: 标题/图例/比例尺/指北针/落款, 无 matplotlib 依赖) ====
_theme_cache = {}

_THEME_BINS = [(DEPTH_THRESH, 0.5, "#9be3ff", "0.05–0.5"), (0.5, 1.0, "#4fc3f7", "0.5–1.0"),
               (1.0, 2.0, "#2196f3", "1.0–2.0"), (2.0, 3.0, "#0d47a1", "2.0–3.0"),
               (3.0, 99.0, "#4a148c", ">3.0")]


def _load_cjk_font(size):
    """中文字体: FLOOD_CJK_FONT 环境变量优先, 依次探测 Windows/Linux/macOS 常见路径。
    全部缺失时退回 PIL 默认字体(Docker python-slim 无中文字体会显示豆腐块,
    Dockerfile 已加 fonts-wqy-microhei; 也可挂载字体并设 FLOOD_CJK_FONT)。"""
    from PIL import ImageFont
    cands = []
    if os.environ.get("FLOOD_CJK_FONT"):
        cands.append(os.environ["FLOOD_CJK_FONT"])
    cands += [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\simsun.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
              "/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/STHeiti Light.ttc"]
    for fp in cands:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


_HAZ_BINS = [(DEPTH_THRESH, 0.5, "#fff59d", "Ⅰ级·低危 (<0.5m)"),
             (0.5, 1.0, "#ffb74d", "Ⅱ级·中危 (0.5-1m)"),
             (1.0, 2.0, "#fb8c00", "Ⅲ级·高危 (1-2m)"),
             (2.0, 1e9, "#c62828", "Ⅳ级·极重 (>2m)")]


def _theme_product_layers(d, z, transform, product):
    """产品着色: 把各产品的淹没掩膜写入 flood_rgb, 返回图例数据。
    (extent 淹没分级 / hazard 危险分级 / facilities 设施情境底色)"""
    rows, cols = d.shape
    flood_rgb = np.zeros((rows, cols, 3), dtype="uint8")
    flood_any = np.zeros((rows, cols), dtype=bool)
    legend_items = []          # [(color, label)]
    haz_counts = []
    fac_flooded = []           # 受淹公共设施(质口水深>阈值)

    def _rgb(m, col):
        r8, g8, b8 = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
        flood_rgb[m] = (r8, g8, b8)

    if product == "hazard":
        for lo, hi, col, lbl in _HAZ_BINS:
            m = (d > lo) & (d <= hi) & (z > 0)
            _rgb(m, col)
            flood_any |= m
            legend_items.append((col, lbl))
            haz_counts.append(int(m.sum()))
    elif product == "facilities":
        m = (d > DEPTH_THRESH) & (z > 0)     # 情境底色: 陆域淹没淡蓝
        flood_rgb[m] = (185, 222, 248)
        flood_any |= m
        for b in _get_buildings():
            if b["btype"] != "公共":
                continue
            dv = _sample_grid(d, transform, b["lon"], b["lat"])
            if dv is not None and dv > DEPTH_THRESH:
                fac_flooded.append({"name": b["name"] or "公共设施",
                                    "lon": b["lon"], "lat": b["lat"],
                                    "height_m": b["height_m"], "depth_m": round(dv, 2)})
        fac_flooded.sort(key=lambda x: -x["depth_m"])
        legend_items = [("#e53935", "受淹公共设施 (%d 栋)" % len(fac_flooded)),
                        ("#43a047", "未受淹公共设施"),
                        ("#b9def8", "陆域淹没范围(情境)")]
    else:  # extent(默认): 淹没水深分级(与落款"仅陆域"口径一致, 排除常年河道)
        for lo, hi, col, lbl in _THEME_BINS:
            m = (d > lo) & (d <= hi) & (z > 0)
            _rgb(m, col)
            flood_any |= m
            legend_items.append((col, lbl))
    return flood_rgb, flood_any, legend_items, haz_counts, fac_flooded


def _theme_hillshade(z, mw, mh):
    """低分辨率平滑地形晕渲(明暗突出淹没层)。"""
    zmax = max(1.0, float(np.nanmax(z)))
    z_small = np.array(Image.fromarray(
        np.clip((z / zmax * 255), 0, 255).astype("uint8"), "L"
    ).resize((72, 72), Image.BILINEAR)).astype("float32") / 255.0 * zmax
    gz = np.gradient(z_small)
    shade_small = np.clip(0.80 + 9.0 * (gz[0] + gz[1]), 0.62, 1.0)
    return Image.fromarray((shade_small * 255).astype("uint8"), "L").resize((mw, mh), Image.BILINEAR).convert("RGB")


def _theme_blend(px, shade_img, flood_rgb, rows, cols, mw, mh, map_l, map_t):
    """淹没着色与地形晕渲合成: 有淹没以淹没色为主(留 15% 地形明暗), 其余为晕渲底色。"""
    fl_img = Image.fromarray(flood_rgb, "RGB").resize((mw, mh), Image.NEAREST)
    shade_px, fl_px = shade_img.load(), fl_img.load()
    for yy in range(mh):
        for xx in range(mw):
            fr, fg, fb = fl_px[xx, yy]
            if fr or fg or fb:
                sr = shade_px[xx, yy][0] / 255.0
                px[map_l + xx, map_t + yy] = (int(fr * (0.85 + 0.15 * sr)),
                                              int(fg * (0.85 + 0.15 * sr)),
                                              int(fb * (0.85 + 0.15 * sr)))
            else:
                s = shade_px[xx, yy]
                px[map_l + xx, map_t + yy] = (int(s[0] * 0.30 + 0.70 * 226),
                                              int(s[1] * 0.30 + 0.70 * 233),
                                              int(s[2] * 0.30 + 0.70 * 240))


def _theme_grid_and_frame(draw, map_l, map_t, map_r, map_b, mw, mh):
    """外框 + 3×3 分区虚线网格。"""
    draw.rectangle([map_l, map_t, map_r, map_b], outline="#37474f", width=2)
    for i in (1, 2):
        gx = map_l + int(mw * i / 3)
        for yy in range(map_t, map_b, 7):
            draw.line([(gx, yy), (gx, min(yy + 4, map_b))], fill="#607d8b", width=1)
        gy = map_t + int(mh * i / 3)
        for xx in range(map_l, map_r, 7):
            draw.line([(xx, gy), (min(xx + 4, map_r), gy)], fill="#607d8b", width=1)


def _theme_decorate(draw, map_l, map_t, map_r, map_b, mw, mh, product, return_period,
                    title, west, north, east, south, transform, fac_flooded):
    """标题块/落款/指北针/比例尺/facilities 设施点位(f_prod 专用)。"""
    f_title = _load_cjk_font(26)
    f_med = _load_cjk_font(15)
    f_sm = _load_cjk_font(12)
    ttl = title or {"extent": "珠江新城 %d 年一遇暴雨洪涝风险专题图" % return_period,
                    "facilities": "珠江新城 %d 年一遇暴雨·受影响关键设施图" % return_period,
                    "hazard": "珠江新城 %d 年一遇暴雨·洪涝危险分级图" % return_period}[product]
    draw.text((map_l, 40), ttl, font=f_title, fill="#102a43")
    draw.text((map_l, 14), "C2132 · 基于WebGIS的三维城市降雨洪涝可视化表达", font=f_med, fill="#486581")
    # 制图时间用情景结果文件的生成时间(而非请求时刻), 缓存字节流才稳定
    try:
        gen_at = datetime.datetime.fromtimestamp(
            os.path.getmtime(os.path.join(ROOT, "flood_out", "flood_depth_%dy.tif" % return_period))
        ).strftime("%Y-%m-%d %H:%M")
    except OSError:
        gen_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    draw.text((map_l, map_b + 16), "制图: 浴缸法体积注水反演(仅陆域, 排除珠江河道) · 数据: 情景模拟结果, 仅供演示 · 结果生成: " + gen_at,
              font=f_sm, fill="#627d98")
    draw.text((map_l, map_b + 38), "坐标系: WGS84 经纬度 · 分区: 3×3 网格 · 深色为珠江河道掩膜区", font=f_sm, fill="#9fb3c8")
    # 指北针
    nx, ny = map_r - 40, map_t + 56
    draw.polygon([(nx, ny - 34), (nx - 9, ny + 8), (nx, ny - 2)], fill="#263238")
    draw.polygon([(nx, ny - 34), (nx + 9, ny + 8), (nx, ny - 2)], fill="#b0bec5")
    draw.text((nx - 6, ny + 12), "N", font=f_med, fill="#263238")
    # 比例尺: 按画布坐标换算(数据区被 resize 到 mw×mh, 旧版用原始栅格像元算短了 7.7 倍);
    # 图幅纵向按纬度比例略有压缩, 故标注"横向"
    sx0, sy = map_l + 24, map_b - 34
    km_per_px = ((east - west) * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(CELL_LAT))
                 / mw / 1000.0)
    sb_px = max(20, int(1.0 / km_per_px))     # 1 km 对应的画布像素
    draw.line([(sx0, sy), (sx0 + sb_px, sy)], fill="#263238", width=5)
    draw.line([(sx0, sy - 6), (sx0, sy + 6)], fill="#263238", width=3)
    draw.line([(sx0 + sb_px, sy - 6), (sx0 + sb_px, sy + 6)], fill="#263238", width=3)
    draw.text((sx0 + sb_px // 2 - 26, sy - 30), "1 km (横向)", font=f_med, fill="#263238")
    # facilities 产品: 公共设施点位(红=受淹, 绿=未受淹)
    if product == "facilities":
        for b in _get_buildings():
            if b["btype"] != "公共":
                continue
            fx = map_l + int((b["lon"] - west) / (east - west) * mw)
            fy = map_t + int((north - b["lat"]) / (north - south) * mh)
            wet = any(f["lon"] == b["lon"] and f["lat"] == b["lat"] for f in fac_flooded)
            col = "#e53935" if wet else "#43a047"
            draw.ellipse([fx - 7, fy - 7, fx + 7, fy + 7], fill=col,
                         outline="#ffffff", width=2)
        for f in fac_flooded[:5]:
            fx = map_l + int((f["lon"] - west) / (east - west) * mw)
            fy = map_t + int((north - f["lat"]) / (north - south) * mh)
            draw.text((fx + 10, fy - 8), f["name"], font=f_sm, fill="#b71c1c")


def _theme_legend(draw, lx, map_t, product, legend_items, haz_counts, fac_flooded,
                  transform, f_med, f_sm):
    """右侧图例栏(含 hazard 分级统计 / facilities Top5)。"""
    if product == "extent":
        draw.text((lx, map_t + 4), "淹没水深 (m)", font=f_med, fill="#102a43")
    elif product == "hazard":
        draw.text((lx, map_t + 4), "危险分级 (陆域水深)", font=f_med, fill="#102a43")
    else:
        draw.text((lx, map_t + 4), "公共设施影响", font=f_med, fill="#102a43")
    yy = map_t + 40
    for col, lbl in legend_items:
        draw.rectangle([lx, yy, lx + 34, yy + 22], fill=col, outline="#78909c")
        draw.text((lx + 44, yy + 3), lbl, font=f_med, fill="#334e68")
        yy += 36
    yy += 14
    if product == "hazard":
        draw.text((lx, yy), "分级像元数 / 面积估算", font=f_sm, fill="#627d98")
        yy += 22
        cell_km2 = cell_area_m2(transform) / 1e6
        for (col, lbl), cnt in zip(legend_items, haz_counts):
            draw.text((lx, yy), "%s: %d 格 ≈ %.3f km²" % (lbl.split("·")[0], cnt,
                      cnt * cell_km2),
                      font=f_sm, fill="#334e68")
            yy += 20
        yy += 6
    if product == "facilities":
        draw.text((lx, yy), "受淹设施 Top5 (质口水深)", font=f_sm, fill="#627d98")
        yy += 22
        for f in fac_flooded[:5]:
            nm = f["name"] if len(f["name"]) <= 12 else f["name"][:11] + "…"
            draw.text((lx, yy), "%s  %.2fm" % (nm, f["depth_m"]), font=f_sm, fill="#334e68")
            yy += 20
        yy += 6
    draw.text((lx, yy), "底图: DTM 地形晕渲", font=f_sm, fill="#627d98")
    yy += 24
    draw.text((lx, yy), "虚线: 3×3 分区", font=f_sm, fill="#627d98")


def _theme_map_png(return_period, title, product, d, z, transform):
    """渲染专题图 PNG 字节流(三种产品共用制图模板)。"""
    rows, cols = d.shape
    west = transform.c
    north = transform.f
    east = west + transform.a * cols
    south = north + transform.e * rows
    W, H = 1430, 1000
    MAP_L, MAP_T, MAP_R, MAP_B = 70, 110, 1180, 880
    mw, mh = MAP_R - MAP_L, MAP_B - MAP_T
    img = Image.new("RGB", (W, H), "#f5f7fa")
    px = img.load()
    draw = ImageDraw.Draw(img)
    flood_rgb, _flood_any, legend_items, haz_counts, fac_flooded = _theme_product_layers(
        d, z, transform, product)
    shade_img = _theme_hillshade(z, mw, mh)
    _theme_blend(px, shade_img, flood_rgb, rows, cols, mw, mh, MAP_L, MAP_T)
    _theme_grid_and_frame(draw, MAP_L, MAP_T, MAP_R, MAP_B, mw, mh)
    f_med = _load_cjk_font(15)
    f_sm = _load_cjk_font(12)
    _theme_decorate(draw, MAP_L, MAP_T, MAP_R, MAP_B, mw, mh, product, return_period,
                    title, west, north, east, south, transform, fac_flooded)
    _theme_legend(draw, MAP_R + 36, MAP_T, product, legend_items, haz_counts,
                  fac_flooded, transform, f_med, f_sm)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/api/thematic_map", tags=["专题图"])
def thematic_map(return_period: int = Query(100, ge=2, le=100),
                 title: str = Query(None, max_length=60),
                 product: str = Query("extent", pattern="^(extent|facilities|hazard)$")):
    """Copernicus EMS 风格专题图产品系列(map series):
    extent=淹没范围(默认) / facilities=受影响关键设施 / hazard=洪涝危险分级。
    三种产品共用一套制图模板(标题块/3×3分区/指北针/比例尺/图例/落款), 成套交付。
    缓存前置(命中不读栅格); 自定义标题不进缓存(key 含用户输入, 防缓存膨胀)。
    渲染逻辑在 _theme_map_png 及其 5 个 _theme_* 辅助函数。"""
    key = (return_period, product)
    if not title and key in _theme_cache:
        return Response(content=_theme_cache[key], media_type="image/png")
    d = _load_depth_tif(return_period)
    if d is None:
        return JSONResponse({"error": "无 %d 年水深栅格" % return_period}, status_code=404)
    z, transform = _get_dtm()
    png = _theme_map_png(return_period, title, product, d, z, transform)
    if not title:
        _theme_cache[key] = png
    return Response(content=png, media_type="image/png")


# ==== 实时雨情(演示数据: 每10分钟一个确定性情景, 可替换为真实气象接口) ====
_STATIONS = [("猎德大道站", 113.3230, 23.1125), ("花城大道站", 113.3295, 23.1170),
             ("珠江公园站", 113.3355, 23.1195), ("海心沙站", 113.3180, 23.1095),
             ("员村站", 113.3420, 23.1135)]


@app.get("/api/realtime_rain", tags=["实时雨情"])
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
    # 最近 12 小时趋势: hours[-12:] 对应 now-11h..now, 标签须与值同时序
    trend = [{"hour": (datetime.datetime.now() - datetime.timedelta(hours=11 - i)).strftime("%H:%M"),
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


@app.post("/api/assistant", tags=["智能问答"])
def assistant(payload: dict = Body(...)):
    """防汛问答: 规则解析(重现期/雨量模拟/易涝点/影响/损失/预警/疏散/原理), 全离线。"""
    q = str(payload.get("question", "")).strip()
    if not q:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    import re as _re

    def has(*words):
        return any(w in q for w in words)

    def scen(T):
        p = os.path.join(ROOT, "flood_out", "scenarios.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
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
        z0, tr0 = _get_dtm()
        im = impact_stats(dt, tr0) if dt is not None else {}
        land_km2 = float((z0 > 0).sum()) * cell_area_m2(tr0) / 1e6   # 陆域面积(河道不计)
        pct = 100.0 * s["flooded_area_km2"] / land_km2 if land_km2 else 0.0
        ans = ("%d年一遇(24h设计雨量 %s mm): 反演水位 %.2f m, 淹没 %.2f km²(占陆域约 %.0f%%), "
               "平均/最大水深 %.2f/%.2f m; 受影响建筑 %s/%s 栋、约 %s, 估算直接经济损失约 %.0f 万元。"
               % (T, s["rain_mm"], s["water_level_m"], s["flooded_area_km2"], pct,
                  s["mean_depth_m"], s["max_depth_m"],
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
        ans = ("已筛选 %d 处避难场所(公共设施/未受淹高层), 并为 %d 处主要易涝点规划了疏散路径(水深≥0.8m 阻断, "
               "0.5–0.8m 高代价, 0.15–0.5m 涉水): "
               % (len((ev or {}).get("shelters", [])), len(rts))) + \
              "; ".join("→%s(%d米)" % (r["to"]["name"], r["distance_m"]) for r in rts[:3]) + \
              "。路径已在三维场景绘制。"
        return {"answer": ans, "data": ev or {}, "action": {"type": "evacuation", "return_period": 100}}

    if has("四预", "预报", "预演", "预案", "数字孪生"):
        return {"answer": "本平台按水利数字孪生「四预」体系组织功能: "
                          "①预报—P-III 设计暴雨与 Gumbel 重现期拟合, 2/5/10/50/100 年情景与自定义雨量即输即得; "
                          "②预警—按全市淹没面积与分区占比自动发布蓝/黄/橙/红四级预警(顶部预警条); "
                          "③预演—在线模拟实时推演自定义雨量/径流系数(海绵城市 C 值), 双屏对比支撑方案比选; "
                          "④预案—A* 避水疏散路径、避难场所与孤岛待援识别, 关键设施影响清单辅助处置。",
                "data": {}}

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
@app.post("/api/report", tags=["公众报汛"])
def report_create(request: Request, payload: dict = Body(...)):
    try:
        ip = _client_ip(request)
        if not _rate_ok(ip):
            return JSONResponse({"error": "提交过于频繁, 请稍后再试"}, status_code=429)
        # 类型错乱直接 400(不回显内部异常); 文本剥标签防存储型 XSS
        cat = payload.get("category", "积水")
        if not isinstance(cat, str) or cat not in ("积水", "倒灌", "道路封闭", "其他"):
            cat = "其他"
        try:
            lon = float(payload["lon"]) if payload.get("lon") is not None else None
            lat = float(payload["lat"]) if payload.get("lat") is not None else None
        except (TypeError, ValueError):
            return JSONResponse({"error": "经纬度格式不正确"}, status_code=400)
        item = {"lon": lon, "lat": lat,
                "location_text": _clean_text(payload.get("location_text", ""), 120),
                "category": cat,
                "depth_est": _clean_text(payload.get("depth_est", ""), 20),
                "desc": _clean_text(payload.get("desc", ""), 300),
                "status": "待核实",
                "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        img = payload.get("image") or ""
        img_data = None
        if isinstance(img, str) and img.startswith("data:image") and "," in img and len(img) < 3_500_000:
            try:
                img_data = base64.b64decode(img.split(",", 1)[1])
            except Exception:
                img_data = None
        # 读-改-写全程持 _report_lock: 并发提交不丢记录、不撞 id
        # (照片 CPU 解码在锁外, 仅文件落盘在锁内)
        with _report_lock:
            lst = _load_reports()
            rid = "r" + uuid.uuid4().hex[:12]
            item["id"] = rid
            if img_data is not None:
                try:
                    ext = ".jpg" if "jpeg" in img[:32] else ".png"
                    fn = "img_%s%s" % (rid, ext)
                    Image.open(io.BytesIO(img_data)).convert("RGB").save(os.path.join(_REPORT_DIR, fn))
                    item["image"] = fn
                except Exception as e:
                    _log.warning("报汛 %s 照片解码/保存失败(其余字段已入库): %s", rid, e)
                    item["image_error"] = "照片保存失败, 其余信息已提交"
            lst.insert(0, item)
            _save_reports(lst[:1000])   # 上限放宽到 1000, 避免静默丢报汛
        return {"ok": True, "id": rid, "item": item}
    except RuntimeError as e:
        # 报讯 store 受损(降级写回保护拒绝覆盖) → 503, 现场可立刻识别是存储问题
        _log.error("报讯存储受损(ip=%s): %s", _client_ip(request), e)
        return JSONResponse({"error": str(e)}, status_code=503)
    except OSError as e:
        # 磁盘满/Windows 文件占用(PermissionError)等系统级写盘故障: 不是用户输入的错,
        # 伪装成 400 会让用户误判"字段格式问题"且无法重试(2026-10 审计 REL-01)
        _log.error("报讯写盘失败(系统级, ip=%s): %s", _client_ip(request), e)
        return JSONResponse({"error": "服务暂时无法保存报讯, 请稍后重试"}, status_code=503)
    except Exception as e:
        try:
            prev = {k: str(v)[:40] for k, v in list(payload.items())[:6]}
        except Exception:
            prev = {}
        _log.warning("报汛提交被拒(ip=%s): %s | payload=%s",
                     _client_ip(request), e, prev)
        return JSONResponse({"error": "提交失败: 请检查字段格式"}, status_code=400)


@app.get("/api/report", tags=["公众报汛"])
def report_list(limit: int = Query(50, ge=1, le=200), status: str = Query(None)):
    """公众上报列表; status 参数可过滤(如 status=已核实 供三维场景上图)。
    total 为过滤后总数(切片前统计), 分页语义正确。"""
    lst = _load_reports()
    if status:
        lst = [x for x in lst if x.get("status") == status]
    total = len(lst)
    lst = lst[:limit]
    for it in lst:
        if it.get("image"):
            it["image_url"] = "/reports/" + it["image"]
    return {"reports": lst, "total": total}


@app.post("/api/report/{rid}/status", tags=["公众报汛"])
def report_status(rid: str, payload: dict = Body(...), request: Request = None,
                  token: str = Query(None)):
    if not _require_admin(_token_from(request, token)):
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


# ==== 订阅式预警(参考 Google Flood Hub Email Subscriptions; 演示为本地登记+模拟触达) ====
_EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+")


@app.post("/api/subscribe", tags=["订阅预警"])
def subscribe(request: Request, payload: dict = Body(...)):
    """登记预警订阅: 邮箱 + 关注分区(区1-区9 或 '全部')。演示环境仅本地登记, 不发送真实邮件。
    灌库防护(2026-10 审计 SEC-04): 3 次/分钟频控 + 总量上限 409 + 白名单式邮箱校验 +
    入库前 _clean_text 剥标签(订阅表仅 admin 侧读取, XSS 属潜伏风险, 入口即消毒)。"""
    if not _rate_ok("sub:" + _client_ip(request), limit=3, window=60.0):
        return JSONResponse({"error": "订阅过于频繁, 请稍后再试"}, status_code=429)
    email = _clean_text(str(payload.get("email", "")).strip(), 120)
    zone = _clean_text(str(payload.get("zone", "全部")).strip(), 20) or "全部"
    if not _EMAIL_RE.fullmatch(email):
        return JSONResponse({"error": "邮箱格式不正确"}, status_code=400)
    with _subscribers_lock:
        subs = _load_subscribers()
        for s in subs:
            if s["email"] == email:
                s["zone"] = zone
                break
        else:
            if len(subs) >= SUBSCRIBER_CAP:
                return JSONResponse({"error": "订阅数已达上限"}, status_code=409)
            subs.append({"email": email, "zone": zone,
                         "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        _save_subscribers(subs)
    return {"ok": True, "total": len(subs),
            "note": "已登记; 演示环境不发送真实邮件, 预警触发时在 /api/warning 返回触达统计"}


@app.get("/api/subscribe", tags=["订阅预警"])
def subscribe_list(request: Request, token: str = Query(None)):
    if not _require_admin(_token_from(request, token)):
        return JSONResponse({"error": "需要管理员登录"}, status_code=401)
    return {"subscribers": _load_subscribers(), "total": len(_load_subscribers())}


# ---------------- 前端静态托管(安全白名单) ----------------
@app.get("/")
def root():
    return RedirectResponse("/welcome.html")


_ALLOWED_EXT = {".html", ".htm", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                ".ico", ".json", ".geojson", ".wasm", ".ktx2", ".basis", ".cur",
                ".ttf", ".woff", ".woff2"}
# 注: .tif/.tiff 不在白名单(原始 DEM/水深栅格不对外静态下载; 前端仅在 unet.html 的
# <input accept> 提交到 /api/predict, 不经静态路由)

# 凭据/隐私文件无论位于哪个目录都不允许静态下载(应只经 API 带鉴权访问)
_SECRET_BASENAMES = {"web_users.json", "subscribers.json", "reports.json"}


class SafeStaticFiles(StaticFiles):
    """静态服务白名单: 只允许前端资源扩展名, 拒绝点文件(.env/.git等)与
    模型/数据库/文档等敏感或大文件(.pt/.npz/.docx/.py/.bat...)被 HTTP 直接下载;
    另设 basename 黑名单拦截凭据/隐私 JSON(web_users/subscribers/reports)。"""

    def lookup_path(self, path):
        # Windows 下 Starlette 会把分隔符反斜杠化(如 reports\subscribers.json),
        # 必须先统一为 "/" 再判定, 否则黑名单与点文件检查均可被绕过
        norm = path.replace("\\", "/")
        parts = [p for p in norm.split("/") if p]
        if any(p.startswith(".") for p in parts):
            return "", None
        if parts and parts[-1].lower() in _SECRET_BASENAMES:
            return "", None
        ext = os.path.splitext(norm)[1].lower()
        if ext and ext not in _ALLOWED_EXT:
            return "", None
        return super().lookup_path(path)


app.mount("/", SafeStaticFiles(directory=ROOT, html=False), name="static")
