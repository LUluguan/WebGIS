# -*- coding: utf-8 -*-
"""download_web_libs.py — 下载 Cesium 1.95 + ECharts 5.5 到 web/ 本地化, 消除 jsdelivr CDN 依赖。
用法: D:/python.exe tools/download_web_libs.py
"""
import os, requests

BASE_CDN = "https://cdn.jsdelivr.net/npm"
DATA_CDN = "https://data.jsdelivr.com/v1/packages/npm"
CESIUM_VER = "1.95.0"
ECHARTS_VER = "5.5.0"
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def download(url, dest):
    if os.path.exists(dest):
        print("skip", os.path.relpath(dest))
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    print("ok  ", os.path.relpath(dest), len(r.content), "B")


def main():
    # ECharts 单文件
    download("%s/echarts@%s/dist/echarts.min.js" % (BASE_CDN, ECHARTS_VER),
             os.path.join(WEB, "echarts.min.js"))
    # Cesium 文件树(通过 jsdelivr data API 枚举)
    tree = requests.get("%s/cesium@%s" % (DATA_CDN, CESIUM_VER), timeout=60).json()
    acc = []

    def walk(fs, prefix=""):
        for f in fs:
            p = prefix + "/" + f["name"] if prefix else f["name"]
            if f.get("type") == "directory":
                walk(f.get("files", []), p)
            elif p.startswith("Build/Cesium/") and not p.endswith(".map"):
                acc.append(p)

    walk(tree.get("files", []))
    for p in acc:
        rel = p[len("Build/Cesium/"):]
        url = "%s/cesium@%s/Build/Cesium/%s" % (BASE_CDN, CESIUM_VER, rel)
        download(url, os.path.join(WEB, "cesium", rel.replace("/", os.sep)))
    print("完成: %d 个 Cesium 文件 + ECharts" % len(acc))


if __name__ == "__main__":
    main()
