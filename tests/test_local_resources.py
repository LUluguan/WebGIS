# -*- coding: utf-8 -*-
"""本地资源检查: 页面不得引用 CDN; 页面声明的本地脚本/数据必须真实存在。
2026-10 工程化整理(数据迁入 web/data/)后, 本测试兼作"页面-资源断链"守护。"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "dashboard.html", "flood.html", "realevent.html", "unet.html",
         "welcome.html", "login.html", "mobile.html", "analysis.html", "compare.html",
         "thematic.html"]

def test_local_resources():
    for p in PAGES:
        html = open(os.path.join(ROOT, p), encoding="utf-8").read()
        assert "cdn.jsdelivr.net" not in html, "%s 仍引用 jsdelivr" % p
        assert "unpkg.com" not in html, "%s 仍引用 unpkg" % p
        # 页面引用的本地相对资源(src=/fetch 字符串)必须存在于磁盘(动态拼接的除外:
        # flood.html 的 'web/data/flood_depth_' + T + 'y.png' 以单引号分段, 静态正则取不到完整名)
        refs = set()
        for m in re.finditer(r'''(?:src=|fetch\(\s*)["']([^"']+\.js)["']''', html):
            refs.add(m.group(1))
        for ref in sorted(refs):
            if ref.startswith(("http", "//", "data:", "/api/")):
                continue
            target = ref.lstrip("/")
            assert os.path.exists(os.path.join(ROOT, target)), \
                "%s 引用的本地资源缺失: %s" % (p, ref)
    assert os.path.exists(os.path.join(ROOT, "web", "cesium", "Cesium.js"))
    assert os.path.exists(os.path.join(ROOT, "web", "cesium", "Workers", "cesiumWorkerBootstrapper.js"))
    assert os.path.exists(os.path.join(ROOT, "web", "echarts.min.js"))
    print("本地资源检查 OK(11 页无 CDN, 页面本地脚本引用全部存在)")

if __name__ == "__main__":
    test_local_resources()
    print("test_local_resources OK")
