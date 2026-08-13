# -*- coding: utf-8 -*-
"""export_geoscene.py — 为 ArcGIS Online / GeoScene 发布准备数据:
1) 把 flood_out/flood_extent_{T}y.geojson 转 Shapefile(ogr2ogr)
2) 复制 flood_out/flood_depth_{T}y.tif 到 geoscene_out/
产出后用 ArcGIS Pro 发布为托管要素图层 + 影像图层(见 交付文档/07_GeoScene发布指南.docx)。
"""
import os, glob, shutil, subprocess, sys

ROOT = r"D:\Competiton"
OGR = r"D:\sql\bin\ogr2ogr.exe"
SRC = os.path.join(ROOT, "flood_out")
OUT = os.path.join(ROOT, "geoscene_out")
RETURN_PERIODS = [2, 5, 10, 50, 100]


def main():
    import json
    os.makedirs(OUT, exist_ok=True)
    # ---- 要素: 合并 5 重现期为一个要素类, 加 return_period 属性 ----
    merged = {"type": "FeatureCollection", "features": []}
    for T in RETURN_PERIODS:
        gj = os.path.join(SRC, "flood_extent_%dy.geojson" % T)
        if not os.path.exists(gj):
            print("跳过(缺源):", gj)
            continue
        fc = json.load(open(gj, encoding="utf-8"))
        for feat in fc.get("features", []):
            feat.setdefault("properties", {})["return_yr"] = T   # 字段名≤10字符(shapefile限制)
            merged["features"].append(feat)
        print("合并 %dy: %d 个多边形" % (T, len(fc.get("features", []))))
    mj = os.path.join(OUT, "extent_merged.geojson")
    json.dump(merged, open(mj, "w", encoding="utf-8"), ensure_ascii=False)
    shp = os.path.join(OUT, "extent.shp")
    r = subprocess.run([OGR, "-f", "ESRI Shapefile", "-overwrite", shp, mj],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(shp):
        print("要素 OK: extent.shp (%d 个多边形, 含 return_yr 字段)" % len(merged["features"]))
    else:
        print("要素失败:", (r.stderr or r.stdout)[:300])
    # ---- 栅格: 复制水深 tif ----
    for T in RETURN_PERIODS:
        src_tif = os.path.join(SRC, "flood_depth_%dy.tif" % T)
        if os.path.exists(src_tif):
            shutil.copy2(src_tif, os.path.join(OUT, "depth_%dy.tif" % T))
            print("栅格 OK: depth_%dy.tif" % T)
    print("\n发布文件就绪 ->", OUT)
    print("下一步: 用 ArcGIS Pro 打开 geoscene_out/extent.shp(含 return_period) 与 depth_*.tif,")
    print("发布为 ArcGIS Online 托管要素图层(可按 return_period 查询)与影像图层,")
    print("复制服务 URL 填入 .env 的 GEOSCENE_EXTENT_URL / GEOSCENE_DEPTH_URL。")


if __name__ == "__main__":
    sys.exit(main() or 0)
