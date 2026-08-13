# 基于 WebGIS 的三维城市降雨洪涝可视化

2026 易智瑞杯 GIS 竞赛(C-GIS 组)参赛作品。基于 Vue3 + CesiumJS + UNet 深度学习,
实现不同重现期降雨条件下城市**积水深度与淹没范围**的三维动态表达,并支持**真实洪涝事件**——由
Sentinel-1/Sentinel-2 真实卫星影像经 UNet 水体提取、水位反演得到三维水深。

## 快速开始

### 方式一:Windows 一键脚本(推荐演示)

```bat
git clone <仓库地址> && cd 项目目录
setup.bat      :: 首次: 建虚拟环境 + 安装依赖(含 torch CPU)
run.bat        :: 启动服务并自动打开浏览器 → 欢迎页
```

### 方式二:Docker(跨平台部署)

```bash
docker compose up -d
# 打开 http://localhost:8001/
```

> 镜像约 2-3GB(含 torch CPU)。PostGIS 可选:不配置时服务层自动回退读本地文件,不装数据库也能演示。

### 方式三:命令行

```bash
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu
uvicorn app:app --host 127.0.0.1 --port 8001
```

## 系统架构

```
表现层   CesiumJS 三维场景 / ECharts 数据大屏 / UNet 演示页 / 欢迎落地页
   ↓  REST(/api/*)
服务层   FastAPI(app.py) —— 场景、淹没范围、水深图、降雨、UNet 推理、真实事件元数据
   ↓
数据层   PostgreSQL + PostGIS(可选, 连不上时自动回退读本地文件)
计算层   Python 算法管线(离线): Gumbel 重现期 → 浴缸法水位反演 → 水深
         真实事件管线: 卫星影像 → UNet 水体提取 → 边界水位反演 → 水深
```

前端资源(Cesium 1.95 / ECharts 5.5)已本地化到 `web/`,不依赖 jsdelivr CDN;仅天地图底图需联网。

## 演示流程

欢迎页(`/` 自动进入) → 三大模块:

1. **三维洪涝模拟**(`index.html`):主场景,顶部可切换「模拟 · 珠江新城 / 真实 · 英德」双模式
   - 模拟:2/5/10/50/100 年重现期,浴缸法反演水位与水深
   - 真实:北江 2022-06 英德洪水,UNet 反演水深三维展示
2. **数据大屏**(`dashboard.html`):KPI、逐月降雨态势、水深分布、预警等级
3. **真实事件独立页**(`realevent.html`):四步管线图 + 图层切换 + 方法对比

## 目录结构

```
app.py / welcome.html / index.html / dashboard.html / realevent.html / unet.html
web/                    本地化前端资源(Cesium 1.95 + ECharts 5.5)
realevent_beijiang.py   真实事件主管线(下载卫星影像→UNet→水位反演→导出)
sat_data.py             STAC 检索 + 匿名签名 + 窗口读取重投影
unet_apply.py           5 波段堆栈→UNet 掩膜→水位反演
sar_change.py           SAR 双时相变化检测(验证)
unet_model.py / train_unet.py / infer_unet.py / eval_unet.py   UNet 深度学习
prep_precip.py / prep_return_period.py   降雨预处理 + Gumbel 重现期拟合
fetch_dem.py / bathtub_flood.py / water_level_inversion.py     浴缸法 + 水位反演
export_web.py / export_dashboard.py / export_unet_demo.py       前端数据导出
setup.bat / run.bat     Windows 一键安装/启动
Dockerfile / docker-compose.yml / .dockerignore    Docker 部署
flood_out/              重现期计算结果(水深 tif / 淹没 geojson / scenarios.json)
realevent_out/          真实事件结果(真彩/掩膜/水深 PNG + depth.tif + realevent.json)
dem/                    研究区 DEM(GLO-30)
unet_out/               训练好的 UNet 模型 + 演示样本
tests/                  自动化测试(普通 assert 脚本, D:/python.exe 直接运行)
```

## 核心算法(Route A)

- **重现期情景**: 逐像元「年最大月雨量」拟合 Gumbel 分布 → 重现期雨量 → 径流深 → **浴缸法体积注水**反演水面高程 W → 水深 = W − 地形
- **真实影像(UNet)**: Sentinel-1 RTC(SAR)+ Sentinel-2 光学 构建 5 波段 → **UNet 提取水体/淹没范围** → 边界 DEM 高程中位数**反演水位** W → 水深 = W − 地形

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
python realevent_beijiang.py   # 真实事件管线(需联网下载卫星影像)
```

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/scenarios` | 5 个重现期场景参数 |
| GET | `/api/flood_extent?return_period=100` | 淹没范围 GeoJSON |
| GET | `/api/flood_depth_png?return_period=100` | 水深色带 PNG |
| GET | `/api/monthly_rain` | 研究区逐月降雨(precip_tif 缺失时优雅回退空) |
| GET | `/api/depth_hist` | 水深分布 + 预警等级 |
| GET | `/api/realevent` | 真实事件元数据(UNet 反演水深) |
| POST | `/api/predict` | 上传 5 波段影像 → UNet 水体掩膜 |

交互式文档见 `http://127.0.0.1:8001/docs`。

## 常见问题

- **三维场景白屏/无底图**:三维场景需联网加载天地图底图;Cesium/ECharts 已本地化,jsdelivr 不可用时不再受影响。
- **数据大屏降雨图为空**:`precip_tif/` 体积大未随仓库分发,属预期;其余功能不受影响。
- **torch 安装失败**:手动执行 `pip install torch --index-url https://download.pytorch.org/whl/cpu`。
- **需要 PostGIS**:配置 `.env` 环境变量后重启;服务层优先读库、失败自动回退文件。
- **Docker 构建慢**:镜像含 torch CPU 约 2-3GB,首次构建需下载;可用 `docker compose build --no-cache` 排查。

## 说明

降雨数据为逐月栅格,无逐时强度,故以「年最大月雨量」作年极值;仅 5 年样本,50/100 年属外推;**结果仅供演示**,不可作工程依据。真实事件掩膜经 SAR 暗像元与水体特征验证,可靠性标注于真实事件页。
