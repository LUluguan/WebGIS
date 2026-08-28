# -*- coding: utf-8 -*-
"""
prep_return_period.py — 重现期降雨分析 (P0a)

数据: precip_tif/precip_YYYY.tif (12 波段=逐月, 单位 0.1mm, nodata=-32768, 1200x720)
方法: 逐像元取「年最大月降雨量」作年极值样本 (2021~2025 共 5 个),
     用 Gumbel 分布矩法(MOM)拟合, 推求 2/5/10/50/100 年重现期月雨量。

说明: 原始降雨为逐月栅格, 无逐日/逐时强度, 故以「年最大月雨量」近似年极值。
     仅 5 年样本, 50/100 年属于外推, 结果仅作竞赛演示, 不可作工程依据。

输出: precip_tif/return_period.tif (5 波段 = 2/5/10/50/100 年, 单位 mm, float32)
      并在控制台打印研究区(珠江新城)各重现期代表值。
"""
import os
import numpy as np
import tifffile

ROOT = os.path.dirname(os.path.abspath(__file__))

NODATA = -32768
YEARS = [2021, 2022, 2023, 2024, 2025]
RETURNS = [2, 5, 10, 50, 100]

# 裁剪栅格的地理范围(与 prep_precip.py 一致)
LON_MIN, LON_MAX = 109.0, 119.0
LAT_MIN, LAT_MAX = 20.0, 26.0
RES = 1.0 / 120.0

# 研究区(珠江新城/广州塔, 略放大)
STUDY = (113.30, 113.34, 23.09, 23.13)  # lon_min, lon_max, lat_min, lat_max

GAMMA = 0.5772156649015329  # Euler-Mascheroni


def write_geotiff(path, bands, geo):
    """bands: (nbands, nrows, ncols) float32. geo=(x0,dx,0,y0,0,dy)。"""
    x0, dx, _, y0, _, dy = geo
    extratags = [
        (33550, 'd', 3, (dx, abs(dy), 0.0)),                    # ModelPixelScale
        (33922, 'd', 6, (0, 0, 0, x0, y0, 0)),                  # ModelTiepoint
        (34735, 'H', 16, (1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)),  # WGS84
    ]
    with tifffile.TiffWriter(path, bigtiff=False) as tw:
        tw.write(bands, photometric='minisblack', planarconfig='separate',
                 extratags=extratags)
    print("wrote %s bands=%d %dx%d" % (path, bands.shape[0], bands.shape[2], bands.shape[1]))


def geo_from_first_tif():
    # 由 prep_precip.py 的固定裁剪推得: 左上角 lon[li[0]], lat[ai[0]](lat 降序)
    x0 = LON_MIN
    y0 = LAT_MAX  # 左上角纬度(降序排列, 第一行为 26°N)
    return (x0, RES, 0.0, y0, 0.0, -RES)


def main():
    # 1) 读取 5 年逐月降雨 -> 年最大月雨量 (mm)
    annual_max = []
    for yr in YEARS:
        p = os.path.join(ROOT, "precip_tif", "precip_%d.tif" % yr)
        arr = tifffile.imread(p)                      # (12, 720, 1200) int16
        arr = arr.astype(np.float32)
        arr[arr == NODATA] = np.nan                   # 海洋/无效 -> NaN
        am = np.nanmax(arr, axis=0) * 0.1             # 月最大, 0.1mm -> mm
        annual_max.append(am)
        print("  %d: annual-max month rain mm  min=%.1f max=%.1f nan=%d"
              % (yr, np.nanmin(am), np.nanmax(am), int(np.isnan(am).sum())))
    X = np.stack(annual_max)                          # (5, 720, 1200) mm
    valid = np.isfinite(X).all(axis=0)                # 5 年都有效的像元

    # 2) Gumbel 矩法拟合 (逐像元向量化)
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    beta = std * np.sqrt(6.0) / np.pi                 # 尺度参数
    mu = mean - GAMMA * beta                          # 位置参数
    beta = np.where(beta < 1e-6, 1e-6, beta)          # 防 0

    # 3) 各重现期雨量: x_T = mu - beta*ln(-ln(1-1/T))
    bands = []
    for T in RETURNS:
        p_exceed = 1.0 / T
        xT = mu - beta * np.log(-np.log(1.0 - p_exceed))
        xT = np.where(valid, xT, np.nan)
        bands.append(xT.astype(np.float32))
    out = np.stack(bands)                             # (5, 720, 1200)

    # 4) 写出 GeoTIFF (NaN 作为无效; GDAL 读取后自行处理)
    geo = geo_from_first_tif()
    os.makedirs(os.path.join(ROOT, "precip_tif"), exist_ok=True)
    out_path = os.path.join(ROOT, "precip_tif", "return_period.tif")
    write_geotiff(out_path, out, geo)

    # 5) 研究区代表值(取研究区 2x2 像元的中位数)
    slon_min, slon_max, slat_min, slat_max = STUDY
    c0 = int((slon_min - LON_MIN) / RES)
    c1 = int((slon_max - LON_MIN) / RES) + 1
    r0 = int((LAT_MAX - slat_max) / RES)              # lat 降序
    r1 = int((LAT_MAX - slat_min) / RES) + 1
    print("\n研究区像元范围 rows %d..%d cols %d..%d" % (r0, r1, c0, c1))
    for i, T in enumerate(RETURNS):
        sub = out[i, r0:r1, c0:c1]
        sub = sub[np.isfinite(sub)]
        print("  T=%3dy: 研究区代表雨量 = %.1f mm" % (T, float(np.median(sub))))


if __name__ == "__main__":
    main()
