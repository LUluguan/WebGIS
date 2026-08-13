# -*- coding: utf-8 -*-
import os, sys, numpy as np
os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"
sys.path.insert(0, r"D:\Competiton")
import sat_data

BBOX = [113.357, 24.127, 113.483, 24.253]

def test_stac_sign_read_rtc():
    items = sat_data.stac_search("sentinel-1-rtc", BBOX,
                                 "2022-06-26T00:00:00Z/2022-06-27T00:00:00Z", limit=4)
    assert len(items) > 0, "STAC 检索不到 S1 RTC 洪水中影像"
    f = items[0]
    href = sat_data.sign_url(f["assets"]["vv"]["href"])
    w, h, _ = sat_data.lonlat_bbox_to_grid(BBOX, 32649, 10.0)
    arr = sat_data.read_window(href, BBOX, 32649, w, h)
    assert arr.shape == (h, w), "窗口形状不符: %s" % (arr.shape,)
    fin = np.isfinite(arr)
    assert fin.sum() > 0.1 * fin.size, "有效像元占比过低"
    print("RTC vv 窗口: shape=%s 有效像元=%.1f%% min=%.2f max=%.2f" %
          (arr.shape, 100 * fin.mean(), np.nanmin(arr), np.nanmax(arr)))

if __name__ == "__main__":
    test_stac_sign_read_rtc()
    print("test_sat_data OK")
