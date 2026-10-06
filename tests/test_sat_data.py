# -*- coding: utf-8 -*-
"""sat_data.py 真实链路验证: STAC 检索 → 匿名签名 → 远端 COG 窗口读取。

运行方式(2026-10 性能审计后调整):
  - pytest: 标记 @pytest.mark.network, 默认被 pytest.ini 过滤(日常回归不撞外网, 全套约 10s);
    验证真实链路时显式运行 pytest -m network
  - 直接运行: python tests/test_sat_data.py → _net_ok() 探测 + 5xx/429 归 SKIP(离线不误报)
"""
import os, sys, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(sys.path[0])
import proj_fix  # noqa: F401  PROJ 冲突修复
import pytest

import sat_data

BBOX = [113.357, 24.127, 113.483, 24.253]

def _net_ok():
    """网络可达性探测: Planetary Computer 不可达时跳过(决赛现场离线环境不误报)。"""
    import socket
    try:
        s = socket.create_connection(("planetarycomputer.microsoft.com", 443), timeout=4)
        s.close()
        return True
    except Exception:
        return False

@pytest.mark.network
def test_stac_sign_read_rtc():
    if not _net_ok():
        pytest.skip("Planetary Computer 网络不可达(离线环境自动跳过)")
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
    if not _net_ok():
        print("SKIP test_sat_data: Planetary Computer 网络不可达(离线环境自动跳过)")
    else:
        try:
            test_stac_sign_read_rtc()
            print("test_sat_data OK")
        except Exception as e:
            # 外部服务瞬断(429/5xx/网关超时)不是本仓代码缺陷, 归为 SKIP 而非 FAIL
            import requests as _rq
            if isinstance(e, _rq.HTTPError) and e.response is not None and e.response.status_code in (429, 500, 502, 503, 504):
                print("SKIP test_sat_data: PC 服务暂不可用(HTTP %d), 与本仓代码无关" % e.response.status_code)
            else:
                raise
