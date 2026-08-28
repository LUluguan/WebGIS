# -*- coding: utf-8 -*-
"""
prep_precip.py — 将广东降雨 NetCDF(pre_YYYY.nc)裁剪到 DEM 覆盖范围并写出 12 波段 GeoTIFF。
输出: precip_tif/precip_YYYY.tif (12 波段 = 1-12 月, 单位 0.1mm, 值×0.1=mm, nodata=-32768)
用 tifffile 写出,确保 raster2pgsql/GDAL 可读。
"""
import os, sys
import numpy as np

if os.path.isdir(r"D:\Lib\site-packages"):   # 便携 Python 本机 site-packages 修复(其它机器自动跳过)
    sys.path.insert(0, r"D:\Lib\site-packages")
ROOT = os.path.dirname(os.path.abspath(__file__))
import netCDF4
import tifffile

LON_MIN, LON_MAX = 109.0, 119.0
LAT_MIN, LAT_MAX = 20.0, 26.0
NODATA = -32768


def write_geotiff(path, bands, geo):
    """bands: (nbands, nrows, ncols) int16. geo=(x0,dx,0,y0,0,dy)。"""
    x0, dx, _, y0, _, dy = geo
    extratags = [
        (33550, 'd', 3, (dx, abs(dy), 0.0)),                    # ModelPixelScale
        (33922, 'd', 6, (0, 0, 0, x0, y0, 0)),                  # ModelTiepoint
        (34735, 'H', 16, (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)),  # WGS84
        (42113, 'd', 1, float(NODATA)),                         # GDAL_NODATA
    ]
    with tifffile.TiffWriter(path, bigtiff=False) as tw:
        tw.write(bands, photometric='minisblack', planarconfig='separate',
                 extratags=extratags)
    print("wrote %s  bands=%d %dx%d" % (path, bands.shape[0], bands.shape[2], bands.shape[1]))


def main():
    with netCDF4.Dataset(os.path.join(ROOT, "pre_2021.nc")) as ds:
        lon = ds.variables["lon"][:]
        lat = ds.variables["lat"][:]
    li = np.where((lon >= LON_MIN) & (lon <= LON_MAX))[0]
    ai = np.where((lat >= LAT_MIN) & (lat <= LAT_MAX))[0]
    x0 = lon[li[0]]; dx = lon[li[1]] - lon[li[0]]
    y0 = lat[ai[0]]; dy = lat[ai[1]] - lat[ai[0]]
    print("clip lon %d cols %.4f..%.4f | lat %d rows %.4f..%.4f" % (
        len(li), lon[li[0]], lon[li[-1]], len(ai), lat[ai[0]], lat[ai[-1]]))
    geo = (x0, dx, 0.0, y0, 0.0, dy)

    os.makedirs(os.path.join(ROOT, "precip_tif"), exist_ok=True)
    for year in [2021, 2022, 2023, 2024, 2025]:
        nc = os.path.join(ROOT, "pre_%d.nc" % year)
        if not os.path.exists(nc):
            print("skip", nc); continue
        with netCDF4.Dataset(nc) as ds:
            pv = ds.variables["pre"]
            mv = pv.missing_value if "missing_value" in pv.ncattrs() else NODATA
            sub = np.asarray(pv[:, ai, li])
        sub = sub.astype(np.int16)
        sub[sub == mv] = NODATA
        out = os.path.join(ROOT, "precip_tif", "precip_%d.tif" % year)
        write_geotiff(out, sub, geo)
        print("  year %d: min=%d max=%d nodata=%d" % (year, int(sub.min()), int(sub.max()), int((sub==NODATA).sum())))


if __name__ == "__main__":
    main()
