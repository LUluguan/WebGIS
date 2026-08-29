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
表现层   CesiumJS 三维场景 / ECharts 数据大屏 / GeoScene 2D 分析页 / UNet 演示页 / 欢迎落地页
   ↓  REST(/api/*)
服务层   FastAPI(app.py) —— 场景、淹没范围、水深图、降雨、分区/影响/易涝点、
         UNet 推理、多事件真实事件元数据、GeoScene 配置
   ↓
数据层   PostgreSQL + PostGIS(可选, 连不上时自动回退读本地文件)
         GeoScene Online 托管要素服务(淹没范围, 可选)
计算层   Python 算法管线(离线): P-III 设计暴雨 → 浴缸法水位反演 → 水深
         真实事件管线: 卫星影像 → UNet 水体提取 → 边界水位反演 → 水深
```

前端资源(Cesium 1.95 / ECharts 5.5)已本地化到 `web/`,不依赖 jsdelivr CDN;仅天地图底图需联网(三源底图可切换容错)。

## 演示流程

欢迎页(`/` 自动进入) → 八大模块:

1. **三维洪涝模拟**(`index.html`):主场景,顶部「模拟 · 珠江新城 / 真实事件 / 在线模拟」三模式
   - 模拟:2/5/10/50/100 年重现期,浴缸法反演水位与水深;易涝点 Top8 点击定位;受影响建筑/人口/直接损失统计
   - **分级预警条**:随情景联动(蓝/黄/橙/红四级,按城区淹没面积阈值自动分级)
   - **疏散分析**:一键筛选避难场所、A* 避水疏散路径(≥0.8m 断行/浅水涉水代价感知)、孤岛待援识别,三维路径绘制
   - **防汛智能问答**(右下角 💬):离线规则引擎,自然语言查询淹没/影响/损失/预警/疏散,回答可联动三维场景
   - **专题图导出**:一键生成标准洪涝风险专题图 PNG(标题/图例/比例尺/指北针/落款)
   - **实时雨情**(右上角):演示数据每 10 分钟一情景,站点雨量 + 12h 逐时趋势
   - 真实事件:北江英德 2022-06 / 梅州蕉岭 2024-06 多事件切换,UNet 反演水深,灾前/灾中 SAR 对比
   - 在线模拟:自定义 24h 雨量与径流系数 C(海绵城市情景),实时反演;24h 淹没演进动画(三角形设计雨型)
2. **数据大屏**(`dashboard.html`):实时雨情条、KPI(含影响与损失)、逐月降雨态势、水深分布、预警等级、分区淹没对比
3. **真实事件独立页**(`realevent.html`):多事件切换 + 四步管线图 + 图层切换 + 方法对比
4. **GeoScene 2D 分析**(`analysis.html`):GeoScene API for JavaScript 消费 GeoScene Online 托管要素服务,重现期过滤与专题渲染
5. **情景双屏对比**(`compare.html`):A/B 双屏任选重现期或自定义雨量,淹没地图 + 统计 + 预警自动对比,输出 Δ 差值表
6. **移动公众报汛**(`mobile.html`):H5 查看分级预警与实时雨情,积水点随手报(自动定位+拍照);管理员可核实/处置/标记误报
7. **用户登录**(`login.html`):admin/admin123(管理员,审核报汛)、public/123456(公众)
8. **应急决策支持**:预警发布、避难场所、疏散路径、孤岛待援(三维场景内体验)

## 目录结构

```
app.py / welcome.html / index.html / dashboard.html / realevent.html / unet.html
        / analysis.html / compare.html / mobile.html / login.html
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
reports/                公众报汛数据(reports.json + 照片, 运行时生成)
web_users.json          用户表(首次登录自动播种演示账号)
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
| GET | `/api/flood_depth_png?return_period=100` | 水深色带 PNG(结果缓存) |
| GET | `/api/monthly_rain` | 研究区逐月降雨(precip_tif 缺失时优雅回退空) |
| GET | `/api/depth_hist?return_period=100` | 水深分布 + 预警等级(仅陆地) |
| GET | `/api/zone_flood?return_period=100&grid=3` | 分区淹没占比(仅陆地) |
| GET | `/api/impact?return_period=100` | 淹没影响: 受影响建筑 + 人口 + 直接经济损失估算 |
| GET | `/api/hotspots?return_period=100&top=8` | 易涝点 Top-N(按面积, 含定位 bbox) |
| GET | `/api/online_sim?rain_mm=200&c=0.5` | 在线模拟: 自定义雨量/径流系数实时反演 |
| GET | `/api/warning?return_period=100` | 分区预警等级(蓝/黄/橙/红) + 城市级预警发布 |
| GET | `/api/evacuation?return_period=100` | 避难场所 + A* 避水疏散路径 + 孤岛待援识别 |
| GET | `/api/thematic_map?return_period=100` | 洪涝风险专题图 PNG(标题/图例/比例尺/指北针) |
| GET | `/api/realtime_rain` | 实时雨情(演示数据, 每 10 分钟一情景) |
| POST | `/api/assistant` | 防汛智能问答(离线规则引擎, 回答带三维联动动作) |
| GET/POST | `/api/report` | 公众报汛列表 / 上报(可附照片) |
| POST | `/api/report/{id}/status` | 报汛状态变更(需管理员 token) |
| POST | `/api/auth/login` | 用户登录(管理员/公众, 返回 token) |
| GET | `/api/auth/me` | 当前登录用户 |
| POST | `/api/auth/logout` | 退出登录 |
| GET | `/api/realevent` | 真实事件注册表(多事件) |
| GET | `/api/realevent/{event_id}` | 真实事件元数据(UNet 反演水深) |
| GET | `/api/realevent_extent?event=yingde` | 真实事件淹没多边形(掩膜矢量化) |
| GET | `/api/geoscene` | GeoScene Online 服务配置 |
| POST | `/api/predict` | 上传 5 波段影像 → UNet 水体掩膜 |

> 静态资源服务使用扩展名白名单(`SafeStaticFiles`): `.env`、模型(`.pt`)、缓存(`.npz`)、
> 文档(`.docx/.md`)、脚本(`.py/.bat`)等敏感或大文件一律 404, 仅前端资源可访问。

交互式文档见 `http://127.0.0.1:8001/docs`。

## 常见问题

- **三维场景白屏/无底图**:三维场景需联网加载天地图底图;Cesium/ECharts 已本地化,jsdelivr 不可用时不再受影响。
- **数据大屏降雨图为空**:`precip_tif/` 体积大未随仓库分发,属预期;其余功能不受影响。
- **torch 安装失败**:手动执行 `pip install torch --index-url https://download.pytorch.org/whl/cpu`。
- **需要 PostGIS**:配置 `.env` 环境变量后重启;服务层优先读库、失败自动回退文件。
- **Docker 构建慢**:镜像含 torch CPU 约 2-3GB,首次构建需下载;可用 `docker compose build --no-cache` 排查。

## 说明

降雨数据为逐月栅格,无逐时强度,故以「年最大月雨量」作年极值;仅 5 年样本,50/100 年属外推;**结果仅供演示**,不可作工程依据。真实事件掩膜经 SAR 暗像元与水体特征验证,可靠性标注于真实事件页。
