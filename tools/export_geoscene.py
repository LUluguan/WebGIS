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
    os.makedirs(OUT, exist_ok=True)
    # ---- 要素: geojson -> shapefile ----
    for T in RETURN_PERIODS:
        gj = os.path.join(SRC, "flood_extent_%dy.geojson" % T)
        shp = os.path.join(OUT, "extent_%dy.shp" % T)
        if not os.path.exists(gj):
            print("跳过(缺源):", gj)
            continue
        # 加 return_period 属性: 用 ogr2ogr 无法直接加字段, 这里生成同名 shapefile(不含该属性);
        # return_period 建议在 ArcGIS Pro 中按图层名或添加字段标注。
        cmd = [OGR, "-f", "ESRI Shapefile", "-overwrite", shp, gj]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(shp):
            print("要素 OK:", os.path.basename(shp))
        else:
            print("要素失败 %dy: %s" % (T, (r.stderr or r.stdout)[:200]))
    # ---- 栅格: 复制水深 tif ----
    for T in RETURN_PERIODS:
        src_tif = os.path.join(SRC, "flood_depth_%dy.tif" % T)
        if os.path.exists(src_tif):
            shutil.copy2(src_tif, os.path.join(OUT, "depth_%dy.tif" % T))
            print("栅格 OK: depth_%dy.tif" % T)
    print("\n发布文件就绪 ->", OUT)
    print("下一步: 用 ArcGIS Pro 打开 geoscene_out/ 的 Shapefile 与栅格,")
    print("发布为 ArcGIS Online 托管要素图层与影像图层, 复制服务 URL 填入 .env 的 GEOSCENE_* 变量。")


if __name__ == "__main__":
    sys.exit(main() or 0)
