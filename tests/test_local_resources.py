# -*- coding: utf-8 -*-
import os, sys, re

ROOT = r"D:\Competiton"
PAGES = ["index.html", "dashboard.html", "flood.html", "realevent.html", "unet.html"]

def test_local_resources():
    for p in PAGES:
        html = open(os.path.join(ROOT, p), encoding="utf-8").read()
        assert "cdn.jsdelivr.net" not in html, "%s 仍引用 jsdelivr" % p
        assert "unpkg.com" not in html, "%s 仍引用 unpkg" % p
    assert os.path.exists(os.path.join(ROOT, "web", "cesium", "Cesium.js"))
    assert os.path.exists(os.path.join(ROOT, "web", "cesium", "Workers", "cesiumWorkerBootstrapper.js"))
    assert os.path.exists(os.path.join(ROOT, "web", "echarts.min.js"))
    print("本地资源检查 OK")

if __name__ == "__main__":
    test_local_resources()
    print("test_local_resources OK")
