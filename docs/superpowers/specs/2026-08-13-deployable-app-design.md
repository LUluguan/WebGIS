# 可演示 · 可部署 · 可运行软件应用 设计

日期：2026-08-13
状态：已获用户批准

## 1. 目标

把广东降雨洪涝 WebGIS 工程打磨成可交付的软件产品：

- **演示**：欢迎落地页开场，一键启动自动打开浏览器；
- **部署**：Windows 一键脚本 + Docker 容器两条路；
- **运行**：新机器 `git clone → setup → run` 即开即演，无需 PostGIS（服务层数据库回退读本地文件）。

## 2. 现状（已核实）

- 上一会话已做基础部署化：`requirements.txt`、`run.bat`、`.env.example`、`.gitignore`、`app.py`（相对路径 + 环境变量 + PostGIS 回退）、`README.md`。
- 关键数据已全部提交：`flood_out/`（数据库回退源）、`realevent_out/`、`unet_out/unet_water.pt`（29.7MB）、`dem/`、`buildings_3d.js`、`dashboard_data.js`。
- 运行时缺口：
  - `precip_tif/`（116MB）被 `.gitignore` 排除 → 新机器 `/api/monthly_rain`（数据大屏降雨图）会失败；
  - `requirements.txt` 缺 `requests`（真实事件管线需要），含未用到的 `torchvision`；
  - 前端依赖 jsdelivr CDN（Cesium 1.95、ECharts 5.5）+ 天地图 WMTS（需联网）。
- 前端页面：`index.html`（主场景，含模拟/真实双模式）、`dashboard.html`（大屏）、`realevent.html`（真实事件独立页）、`unet.html`（UNet 演示）、`flood.html`（早期三维页）。

## 3. 用户决策（已确认）

1. **部署形态**：Windows 一键脚本 **且** Docker 容器化，两者都要。
2. **前端资源**：本地化 Cesium 1.95 + ECharts 5.5（不再依赖 jsdelivr CDN）；天地图底图保持在线（唯一网络依赖）。
3. **演示入口**：新增欢迎落地页 `welcome.html` 作为演示/答辩开场。

## 4. 组件设计

### 4.1 前端资源本地化
- 下载 **Cesium 1.95**（`Cesium.js`、`Widgets/widgets.css`、`Assets/`、`Workers/`、`ThirdParty/`）到 `web/cesium/`。
- 下载 **ECharts 5.5** 到 `web/echarts.min.js`。
- 修改 5 个页面（index/dashboard/flood/realevent/unet.html）的资源引用：
  - `https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Cesium.js` → `/web/cesium/Cesium.js`
  - `https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Widgets/widgets.css` → `/web/cesium/Widgets/widgets.css`
  - `https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js` → `/web/echarts.min.js`
- 天地图 WMTS（`{s}.tianditu.gov.cn`）保持在线。密钥沿用现有 `TDT_KEY` 与 Cesium Ion token。

### 4.2 欢迎落地页 `welcome.html`
- 内容：作品标题、参赛信息（华南农业大学 · 2026 易智瑞杯）、三大模块入口卡片（🌊 三维洪涝模拟 / 📊 数据大屏 / 🧠 真实事件·UNet 反演水深）、技术路线简述、数据说明、操作指引。
- 样式：深蓝科技风，与现有面板（`#panel` 风格）统一；本地化 ECharts/Cesium 前它本身只需纯 HTML/CSS。
- 根路径 `/` 重定向到 `welcome.html`（app.py 的 `root()` 改目标）。

### 4.3 部署脚本

| 文件 | 作用 |
|---|---|
| `setup.bat` | Windows：检查 python → 建 `.venv` → `pip install -r requirements.txt` → torch 用 pytorch cpu 源重装 → 自检 import |
| `run.bat` | Windows：激活 `.venv` → 启动 uvicorn（支持 `FLOOD_PORT`）→ `start http://127.0.0.1:<port>/` 自动开浏览器 |
| `Dockerfile` | `python:3.13-slim` + `pip install -r requirements.txt`（rasterio/tifffile 有 manylinux 轮子，免系统 GDAL）→ 拷贝应用 → `CMD uvicorn app:app --host 0.0.0.0 --port 8001` |
| `docker-compose.yml` | 服务 `webgis`，映射 `8001:8001`，`docker compose up -d` |

### 4.4 运行时健壮性
- `/api/monthly_rain`：`precip_tif/precip_*.tif` 缺失时**优雅回退**——返回空降雨序列（`{"years":[],"months":[],"monthly_rain":{}}`）而非 500；dashboard 前端对空数据容错（图表显示"数据缺失"占位）。
- `app.py` 启动端口读取 `FLOOD_PORT` 环境变量（默认 8001）。
- `requirements.txt` 修订：
  - 新增 `requests`（真实事件管线 `sat_data.py`）；
  - 移除 `torchvision`（运行时不使用）；
  - 保留 torch（`/api/predict` 与真实事件推理用），注释标明 CPU 源安装方式。

### 4.5 文档
- `README.md` 更新：架构图 / 快速开始（Windows 脚本、Docker 两条路）/ 演示流程（欢迎页→三大模块）/ API 列表 / 常见问题（天地图需联网、PostGIS 可选、torch 安装）。

## 5. 成功标准

- ✅ 新机 `git clone → setup.bat → run.bat` → 自动打开欢迎页 → 三大模块全部可演示（不装 PostGIS，数据库回退）。
- ✅ `docker compose up -d` → `http://localhost:8001` 提供相同内容。
- ✅ 页面无 jsdelivr CDN 依赖（Cesium/ECharts 走本地 `/web/`）。
- ✅ 断网时除天地图底图外全部功能可用；`monthly_rain` 缺失数据时前端不白屏。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| Cesium 本地化体积 ~10MB | 一次性下载并提交，可接受 |
| Docker 镜像 ~2-3GB（含 torch cpu） | 演示部署可接受；Dockerfile 合理分层以利用缓存 |
| 天地图底图需联网 | 已确认接受；文档标注 |
| torch 平台安装差异 | setup.bat 用 `--index-url https://download.pytorch.org/whl/cpu`；Docker 用 Linux cpu 轮子 |
| `precip_tif` 缺失影响大屏 | monthly_rain 优雅回退 + 前端空数据容错 |

## 7. 范围外（YAGNI）

- 不做完全离线底图（天地图瓦片缓存体积大、复杂）。
- 不做 PyInstaller 打包成单 exe（torch/rasterio 打包脆弱、体积大）。
- 不做登录鉴权/多用户（演示应用无需）。
- 不迁移数据库 schema（沿用 PostGIS 可选回退）。
