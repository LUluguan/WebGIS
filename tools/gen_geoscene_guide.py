# -*- coding: utf-8 -*-
"""gen_geoscene_guide.py — 生成 交付文档/07_GeoScene发布指南.docx"""
import sys, os
sys.path.insert(0, r"D:\Competiton\tools")
from gen_competition_docs import new_doc, title, heading, para, add_table, table_row, save, OUT


def main():
    doc = new_doc()
    title(doc, "GeoScene / ArcGIS Online 发布指南", "广东降雨洪涝 WebGIS · 满足竞赛\"不脱离GeoScene服务器端产品\"要求")
    heading(doc, "一、目的")
    para(doc, "竞赛要求服务器端部署不能完全脱离 GeoScene 服务器端产品。本工程通过把核心地理数据（淹没范围、水深栅格）发布为 ArcGIS Online 托管服务，由前端 Cesium 消费，使服务端部署具备对 GeoScene（Esri 服务器端基础设施）的真实依赖；未配置时自动回退本地数据以保证演示。")
    heading(doc, "二、前置准备")
    para(doc, "1. ArcGIS Pro（本机已安装于 C:\\Program Files\\ArcGIS\\Pro）；2. ArcGIS Online 账号（学校或竞赛提供的组织账号）；3. 发布数据已就绪：geoscene_out/ 目录（由 tools/export_geoscene.py 生成，含 extent.shp（含 return_yr 字段）与 depth_*.tif）。")
    heading(doc, "三、发布要素图层（淹没范围）")
    para(doc, "1. 打开 ArcGIS Pro → 新建/打开地图 → 添加 geoscene_out/extent.shp（共 1957 个多边形，含 return_yr 字段，取值 2/5/10/50/100，用于按重现期查询）。2. 符号化为蓝色水体样式。3. 右键图层 → 分享 → Web 图层 → 类型选「要素图层（可编辑）」→ 共享到 ArcGIS Online。4. 发布完成后，在图层详情页复制服务 URL，形如：")
    para(doc, "https://services1.arcgis.com/<orgId>/arcgis/rest/services/<folder>/extent_flood/FeatureServer/0", indent=False, cn="Consolas")
    heading(doc, "四、发布影像图层（水深）")
    para(doc, "1. 添加 depth_100y.tif 等水深栅格；建议对每个重现期栅格应用统一的蓝色渐变调色板符号化（水深 0~6m）。2. 右键 → 分享 → Web 图层 → 影像图层 → 发布到 ArcGIS Online。3. 每个重现期发布一个影像服务，复制 ImageServer 服务 URL：")
    para(doc, "https://services1.arcgis.com/<orgId>/arcgis/rest/services/<folder>/depth_{T}y/ImageServer", indent=False, cn="Consolas")
    para(doc, "注：{T} 为占位符（2/5/10/50/100），前端会自动替换为当前重现期。")
    heading(doc, "五、配置 .env")
    add_table(doc, 4, 2, header=["变量", "填写"])
    rows = [
        ("GEOSCENE_EXTENT_URL", "要素服务 URL，如 .../extent_flood/FeatureServer/0"),
        ("GEOSCENE_DEPTH_URL", "影像服务 URL，可含 {T} 占位，如 .../depth_{T}y/ImageServer"),
        ("（其余 FLOOD_* 数据库变量可留空）", "数据库可选，服务层自动回退本地文件"),
    ]
    for i, r in enumerate(rows, start=1):
        table_row(doc.tables[-1], i, list(r))
    para(doc, "保存后重启服务：run.bat 或 uvicorn app:app --host 127.0.0.1 --port 8001。")
    heading(doc, "六、验证")
    para(doc, "1. 访问 http://127.0.0.1:8001/api/geoscene，应返回 enabled=true 及两个服务 URL。2. 打开主场景 index.html，切换重现期，状态栏显示「已从 GeoScene 服务加载 N 个淹没斑块」，水深色带来自 GeoScene 影像服务。3. 若 ArcGIS Online 不可用，/api/geoscene 返回 enabled=false，前端自动回退本地数据。")
    heading(doc, "七、常见问题")
    add_table(doc, 5, 2, header=["问题", "处理"])
    rows = [
        ("CORS 跨域报错", "ArcGIS Online 托管服务默认支持 CORS；若自建 Enterprise 需在服务端开启 Allow Origins"),
        ("按 return_yr 查询无结果", "确认发布图层含 return_yr 整数字段（Shapefile 字段名 ≤10 字符）；查询为 where=return_yr%3D<值>"),
        ("水深不随重现期变化", "确认 GEOSCENE_DEPTH_URL 使用 {T} 模板且各重现期已分别发布"),
        ("离线/未配置时", "enabled=false，前端回退本地 flood_out/ 数据，演示不受影响"),
    ]
    for i, r in enumerate(rows, start=1):
        table_row(doc.tables[-1], i, list(r))
    save(doc, "07_GeoScene发布指南.docx")


if __name__ == "__main__":
    main()
    print("07_GeoScene发布指南 生成完成")
