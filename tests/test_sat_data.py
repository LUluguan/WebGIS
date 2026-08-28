# -*- coding: utf-8 -*-
import os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(sys.path[0])
import proj_fix  # noqa: F401  PROJ 冲突修复
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

def _net_ok():
    """网络可达性探测: Planetary Computer 不可达时跳过(决赛现场离线环境不误报)。"""
    import socket
    try:
        s = socket.create_connection(("planetarycomputer.microsoft.com", 443), timeout=4)
        s.close()
        return True
    except Exception:
        return False

if __name__ == "__main__":
    if _net_ok():
        test_stac_sign_read_rtc()
        print("test_sat_data OK")
    else:
        print("SKIP test_sat_data: Planetary Computer 网络不可达(离线环境自动跳过)")
