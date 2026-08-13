# GeoScene/ArcGIS Online 服务器端依赖集成 设计

日期：2026-08-13
状态：已获用户批准

## 1. 背景与要求

竞赛要求**服务器端部署不能完全脱离 GeoScene 服务器端产品**。本工程目前完全采用开源技术栈（FastAPI + PostGIS + CesiumJS），需引入对 GeoScene 服务器端产品的依赖以满足要求。

用户决策：使用 **ArcGIS Online 托管服务**（GeoScene/Esri 服务器端基础设施），采用**数据服务 + 前端消费**的集成深度。

## 2. 方案

把淹没范围/水深发布为 ArcGIS Online 托管服务，前端 Cesium 消费；FastAPI 保留应用逻辑与静态托管；未配置时回退本地（保证现场演示可用）。

### 2.1 数据导出脚本 `tools/export_geoscene.py`
- 用 `ogr2ogr`（`D:\sql\bin\ogr2ogr.exe`）把 `flood_out/flood_extent_{T}y.geojson`（5 重现期）转换为 Shapefile → `geoscene_out/extent_{T}y.shp`
- 复制 `flood_out/flood_depth_{T}y.tif` → `geoscene_out/`
- 产出供 ArcGIS Pro 发布的前置文件

### 2.2 配置与服务端
- `.env.example` 新增 `GEOSCENE_EXTENT_URL`、`GEOSCENE_DEPTH_URL`
- `app.py` 读取两环境变量，新增 `GET /api/geoscene` 返回 `{enabled, extent_url, depth_url}`；两者任一未配置则 enabled=false

### 2.3 前端消费（index.html 模拟模式）
- 启用时：
  - 淹没范围：`fetch(<extent_url>/query?where=1%3D1&outFields=*&f=geojson)` → Cesium 多边形（ArcGIS Online REST 支持 CORS 浏览器直连）
  - 水深：`Cesium.ArcGisMapServerImageryProvider` 加载影像服务作为水深色带
- 未启用：回退现有 `/api/flood_extent` + `/api/flood_depth_png`

### 2.4 发布指南
- `交付文档/07_GeoScene发布指南.docx`：ArcGIS Pro 打开 Shapefile/栅格 → 要素图层（return_period 属性）+ 影像图层（蓝色调色板符号化）→ 分享为 ArcGIS Online 托管 Web 图层 → 复制服务 URL 填入 .env

## 3. 成功标准
- 未配置 GeoScene：`/api/geoscene` → enabled=false，前端走本地，现有演示不受影响
- 配置后：前端从 GeoScene REST 服务拉取数据（需用户实际发布后验证）
- 服务端部署具备 GeoScene（ArcGIS Online）依赖，满足竞赛要求

## 4. 风险
- 本机无 ArcGIS Online 账号，无法联调；实现为配置驱动 + 回退，发布指南指导用户完成
- ArcGIS Online 需联网与账号；回退机制保证断网/未配置时可演示
