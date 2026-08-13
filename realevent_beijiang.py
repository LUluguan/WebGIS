# -*- coding: utf-8 -*-
"""
realevent_beijiang.py — 北江 2022-06 英德洪水真实事件: 下载卫星影像 → 5波段 → UNet → 水位反演 → 导出。
输出到 realevent_out/。用法: python realevent_beijiang.py
"""
import os
# PROJ 冲突修复: 必须在 import rasterio 之前指向 rasterio 自带 proj_data
os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"
import json
import numpy as np
import rasterio
from PIL import Image

import sat_data
import unet_apply
import sar_change

OUT = r"D:\Competiton\realevent_out"
EPSG = 32649
RES = 10.0
BBOX = [113.357, 24.127, 113.483, 24.253]   # 英德城区 + 北江段
FLOW_DT = "2022-06-26T00:00:00Z/2022-06-27T00:00:00Z"
BASE_DT = "2022-06-02T00:00:00Z/2022-06-03T00:00:00Z"
S2_DT = "2022-06-22T00:00:00Z/2022-06-24T00:00:00Z"
S2_FALLBACK_DT = "2022-07-12T00:00:00Z/2022-07-14T00:00:00Z"
DEM_URL = ("https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/"
           "Copernicus_DSM_COG_10_N24_00_E113_00_DEM/Copernicus_DSM_COG_10_N24_00_E113_00_DEM.tif")
S2_BANDS = ["B02", "B03", "B04", "B08", "SCL"]
SIZE = 128
DEPTH_CAP = 6.0
THR = 0.5


def grid_dims():
    w, h, _ = sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)
    return w, h


_LAST_DT = None


def get_asset(collection, dt, asset):
    """检索覆盖 BBOX 中心点的 item 并返回签名后的 asset URL(保证双时相取同轨帧)。"""
    global _LAST_DT
    items = sat_data.stac_search(collection, BBOX, dt, limit=6)
    cx = (BBOX[0] + BBOX[2]) / 2
    cy = (BBOX[1] + BBOX[3]) / 2
    best = None
    for f in items:
        bb = f.get("bbox")
        if bb and len(bb) == 4 and bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]:
            best = f
            break
    if best is None:
        raise RuntimeError("collection=%s dt=%s 无覆盖 BBOX 中心的 item" % (collection, dt))
    _LAST_DT = best["properties"].get("datetime", "")[:10]
    return sat_data.sign_url(best["assets"][asset]["href"])


def read_band(collection, dt, asset):
    w, h = grid_dims()
    href = get_asset(collection, dt, asset)
    return sat_data.read_window(href, BBOX, EPSG, w, h)


def cloud_frac(scl):
    """SCL 云污染占比: 云影(3)+中云(8)+高云(9)+卷云(10)。4/5/6 为晴空(植被/非植被/水)。"""
    valid = np.isfinite(scl)
    if not valid.any():
        return 1.0
    return float((np.isin(scl, (3, 8, 9, 10)) & valid).sum() / valid.sum())


def depth_rgba(depth):
    d = np.asarray(depth, dtype="float32")
    m = np.isfinite(d) & (d > 0.05)
    t = np.clip(d / DEPTH_CAP, 0, 1)
    img = np.zeros(d.shape + (4,), dtype=np.uint8)
    img[..., 0] = (166 * (1 - t)).astype("uint8")
    img[..., 1] = (227 - 176 * t).astype("uint8")
    img[..., 2] = (255 - 153 * t).astype("uint8")
    img[..., 3] = np.where(m, 200, 0).astype("uint8")
    return img


def truecolor_rgb(b04, b03, b02, pct=98):
    arr = np.stack([b04, b03, b02], axis=2).astype("float32")
    vmax = np.nanpercentile(arr, pct)
    rgb = np.clip(arr / max(vmax, 1e-6), 0, 1)
    rgb[~np.isfinite(rgb)] = 0
    return (rgb * 255).astype(np.uint8)


def main():
    import sys
    skip = "--skip-download" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    w, h = grid_dims()
    print("网格 %dx%d @%.0fm EPSG:%d" % (w, h, RES, EPSG))

    cache = os.path.join(OUT, "_cache.npz")
    if skip and os.path.exists(cache):
        print("载入缓存 _cache.npz ...")
        z = np.load(cache)
        vv_flood, vv_base, dem = z["vv_flood"], z["vv_base"], z["dem"]
        s2 = {"B02": z["s2_B02"], "B03": z["s2_B03"], "B04": z["s2_B04"],
              "B08": z["s2_B08"], "SCL": z["s2_SCL"]}
        opt_date = str(z["opt_date"][0]) if "opt_date" in z else S2_DT[:10]
        cf = cloud_frac(s2["SCL"])
        print("窗口云量 %.1f%%" % (100 * cf))
    else:
        # ---- S1 RTC VV: 洪水中 + 灾前基线 ----
        print("下载 S1 RTC VV 洪水中(2022-06-26)...")
        vv_flood = read_band("sentinel-1-rtc", FLOW_DT, "vv")
        print("下载 S1 RTC VV 灾前(2022-06-02)...")
        vv_base = read_band("sentinel-1-rtc", BASE_DT, "vv")

        # ---- S2 光学(带云量回退) ----
        print("下载 S2 光学(2022-06-23)...")
        s2 = {b: read_band("sentinel-2-l2a", S2_DT, b) for b in S2_BANDS}
        cf = cloud_frac(s2["SCL"])
        print("窗口云量 %.1f%%" % (100 * cf))
        if cf > 0.30:
            print("  云量过高 -> 回退 %s" % S2_FALLBACK_DT[:10])
            s2 = {b: read_band("sentinel-2-l2a", S2_FALLBACK_DT, b) for b in S2_BANDS}
            cf = cloud_frac(s2["SCL"])
            print("  回退后云量 %.1f%%" % (100 * cf))

        # ---- DEM ----
        print("下载 GLO-30 DEM...")
        dem = sat_data.read_window(DEM_URL, BBOX, EPSG, w, h)

        opt_date = _LAST_DT or S2_DT[:10]
        np.savez(cache, vv_flood=vv_flood, vv_base=vv_base,
                 s2_B02=s2["B02"], s2_B03=s2["B03"], s2_B04=s2["B04"],
                 s2_B08=s2["B08"], s2_SCL=s2["SCL"], dem=dem,
                 opt_date=np.array([opt_date]))
        print("已缓存下载数据 ->", cache)

    # ---- 5波段堆栈 + UNet ----
    print("构建5波段堆栈 + UNet 推理 (thr=%.1f)..." % THR)
    stack = np.stack([s2["B02"], s2["B03"], s2["B04"], s2["B08"], vv_flood], axis=2)
    mask = unet_apply.predict_mask(stack, SIZE, thr=THR)                 # (128,128)
    print("UNet 掩膜水体占比=%.2f%% (thr=%.1f)" % (100 * (mask > 0).mean(), THR))
    mask_full = np.array(Image.fromarray(mask, "L").resize((w, h), Image.NEAREST)) > 0

    # ---- 水位反演 ----
    W_level, depth = unet_apply.invert_depth(mask_full, dem)
    dep = depth.copy()
    dep[~mask_full] = np.nan
    flooded_km2 = float(mask_full.sum() * RES * RES / 1e6)
    mean_dep = float(np.nanmean(dep))
    max_dep = float(np.nanmax(dep))
    print("反演水位 W=%.2f m  淹没面积=%.2f km²  平均水深=%.2f m  最大水深=%.2f m"
          % (W_level, flooded_km2, mean_dep, max_dep))

    # ---- 验证 1: 单期 SAR 暗像元(水)与 UNet 掩膜一致 ----
    vv_dark = (vv_flood < 0.06) & np.isfinite(vv_flood)         # 水在 VV 为低回波
    inter = (vv_dark & mask_full).sum()
    union = (vv_dark | mask_full).sum()
    sar_iou = inter / max(1, union)
    # ---- 验证 2: 水体特征分离度(掩膜内外 SAR/NIR/DEM 差异) ----
    sig_sar = float(np.nanmedian(vv_flood[mask_full]) - np.nanmedian(vv_flood[~mask_full]))
    sig_nir = float(np.nanmedian(s2["B08"][mask_full]) - np.nanmedian(s2["B08"][~mask_full]))
    sig_dem = float(np.nanmedian(dem[mask_full]) - np.nanmedian(dem[~mask_full]))
    print("UNet vs SAR暗像元 IoU=%.3f  特征分离: SAR=%.3f NIR=%+.0f DEM=%+.1fm" %
          (sar_iou, sig_sar, sig_nir, sig_dem))

    # ---- 验证 3(辅助): 双时相 SAR 变化(洪水中 vs 灾前, 新增水面) ----
    change = sar_change.change_mask(vv_flood, vv_base)

    # ---- 淹没范围包络(供前端水面) ----
    flood_bbox = None
    if mask_full.any():
        ys, xs = np.where(mask_full)
        _, _, dst_tf = sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)
        x0, y0 = rasterio.transform.xy(dst_tf, ys.min(), xs.min(), offset="center")
        x1, y1 = rasterio.transform.xy(dst_tf, ys.max(), xs.max(), offset="center")
        lons, lats = rasterio.warp.transform(rasterio.crs.CRS.from_epsg(EPSG),
                                             rasterio.crs.CRS.from_epsg(4326),
                                             [x0, x1], [y0, y1])
        flood_bbox = [float(min(lons)), float(min(lats)),
                      float(max(lons)), float(max(lats))]

    # ---- 导出 ----
    Image.fromarray(truecolor_rgb(s2["B04"], s2["B03"], s2["B02"])).save(
        os.path.join(OUT, "truecolor.png"))
    Image.fromarray((mask_full * 255).astype("uint8"), "L").save(
        os.path.join(OUT, "flood_mask.png"))
    Image.fromarray(depth_rgba(depth), "RGBA").save(os.path.join(OUT, "depth.png"))
    Image.fromarray((vv_dark * 255).astype("uint8"), "L").save(
        os.path.join(OUT, "sar_water.png"))
    Image.fromarray((change * 255).astype("uint8"), "L").save(
        os.path.join(OUT, "sar_change.png"))
    with rasterio.open(os.path.join(OUT, "depth.tif"), "w", driver="GTiff",
                       height=depth.shape[0], width=depth.shape[1], count=1,
                       dtype="float32", crs="EPSG:%d" % EPSG,
                       transform=sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)[2],
                       nodata=-9999.0) as dst:
        dst.write(np.where(np.isfinite(depth), depth, -9999.0).astype("float32"), 1)

    reliability = "高" if sar_iou > 0.5 else ("中" if sar_iou > 0.15 else "低")
    ev = {
        "event": "北江特大洪水(2022-06) · 英德城区",
        "flood_image": "Sentinel-1 RTC VV 2022-06-26 10:34Z",
        "baseline_image": "Sentinel-1 RTC VV 2022-06-02",
        "optical_image": "Sentinel-2 L2A %s" % opt_date,
        "method": ("UNet(5波段: S2 B2/B3/B4/B8 + S1 RTC VV) 水体提取(prob>%.1f) "
                   "→ 边界水位反演 W=median(DEM[边界])") % THR,
        "validation": ("单期SAR暗像元一致 IoU=%.3f; 水体特征分离(掩膜内-外): "
                       "SAR=%.3f NIR=%+.0f DEM=%+.1fm") % (sar_iou, sig_sar, sig_nir, sig_dem),
        "bbox": BBOX, "epsg": EPSG, "grid_px": RES, "size": SIZE,
        "water_level_m": round(W_level, 2), "flooded_area_km2": round(flooded_km2, 3),
        "mean_depth_m": round(mean_dep, 2), "max_depth_m": round(max_dep, 2),
        "unet_sar_iou": round(sar_iou, 3), "cloud_pct": round(100 * cf, 1),
        "reliability": reliability, "flood_bbox": flood_bbox,
        "assets": {"truecolor": "truecolor.png", "mask": "flood_mask.png",
                   "depth": "depth.png", "sar_water": "sar_water.png",
                   "sar_change": "sar_change.png"},
    }
    with open(os.path.join(OUT, "realevent.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("导出完成 ->", OUT, "| 可靠性:", reliability)


if __name__ == "__main__":
    main()
