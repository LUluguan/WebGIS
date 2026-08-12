# 基于 WebGIS 的三维城市降雨洪涝可视化

华南农业大学 · 2026 易智瑞杯 GIS 竞赛(C-GIS 组)参赛作品。基于 Vue3 + CesiumJS + UNet 深度学习,实现不同重现期降雨条件下城市**积水深度与淹没范围**的三维动态表达。

## 系统架构

```
表现层   CesiumJS 三维场景 / ECharts 数据大屏 / UNet 演示页
   ↓  REST(/api/*)
服务层   FastAPI(app.py) —— 场景、淹没范围、水深图、降雨、UNet 推理
   ↓
数据层   PostgreSQL + PostGIS(可选, 连不上时自动回退读本地文件)
计算层   Python 算法管线(离线): Gumbel 重现期 → 浴缸法水位反演 → 水深
```

## 目录结构

```
app.py                  服务层(FastAPI)
index.html              三维场景(入口, CesiumJS)
dashboard.html          数据大屏(ECharts)
unet.html               UNet 水体提取演示页
unet_model.py / train_unet.py / infer_unet.py / eval_unet.py   UNet 深度学习
prep_precip.py / prep_return_period.py   降雨预处理 + Gumbel 重现期拟合
fetch_dem.py / bathtub_flood.py / water_level_inversion.py     浴缸法 + 水位反演
export_web.py / export_dashboard.py / export_unet_demo.py       前端数据导出
flood_out/              计算结果(水深 tif / 淹没 geojson / scenarios.json)
dem/                    研究区 DEM(GLO-30)
unet_out/               训练好的 UNet 模型 + 演示样本
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
# UNet 推理需要 torch(CPU 版)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

### 2. 启动

Windows 双击 `run.bat`,或命令行:

```bash
uvicorn app:app --host 127.0.0.1 --port 8001
```

打开 `http://127.0.0.1:8001/`(三维场景)。

### 3. 数据库(可选)

默认连本地 `flood_analysis` 库;连不上时服务层**自动回退**读取 `flood_out/` 本地文件,无需数据库也能跑。数据库连接可用环境变量覆盖(见 `.env.example`)。

## 核心算法(Route A)

- **重现期情景**: 逐像元「年最大月雨量」拟合 Gumbel 分布 → 重现期雨量 → 径流深 → **浴缸法体积注水**反演水面高程 W → 水深 = W − 地形
- **真实影像**: **UNet** 提取水体/淹没范围 → 边界 DEM 高程中位数**反演水位** W → 水深 = W − 地形

两条路径共用「水位 − 地形 = 水深」,DEM 用 Copernicus GLO-30(已做建筑剔除)。

## 数据管线(重新生成数据)

原始数据(不随仓库分发):`pre_YYYY.nc`(逐月降雨)、GF-FloodNet(UNet 训练)、SRTM/GLO-30 DEM。

```bash
python prep_precip.py          # nc → 12 波段 GeoTIFF
python prep_return_period.py   # Gumbel 拟合 → 重现期雨量
python fetch_dem.py            # 拉取 GLO-30 研究区窗口
python bathtub_flood.py        # 浴缸法 → 水深/淹没范围/scenarios
python load_flood_pg.py        # 结果入库 PostGIS
python export_web.py           # 导出前端数据(JS/PNG)
python train_unet.py           # 训练 UNet(CPU 子集示例)
```

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/scenarios` | 5 个重现期场景参数 |
| GET | `/api/flood_extent?return_period=100` | 淹没范围 GeoJSON |
| GET | `/api/flood_depth_png?return_period=100` | 水深色带 PNG |
| GET | `/api/monthly_rain` | 研究区逐月降雨 |
| GET | `/api/depth_hist` | 水深分布 + 预警等级 |
| POST | `/api/predict` | 上传 5 波段影像 → UNet 水体掩膜 |

交互式文档见 `http://127.0.0.1:8001/docs`。

## 说明

降雨数据为逐月栅格,无逐时强度,故以「年最大月雨量」作年极值;仅 5 年样本,50/100 年属外推,**结果仅供演示**,不可作工程依据。
