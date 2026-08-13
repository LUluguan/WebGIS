# 可演示·可部署·可运行软件应用 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工程打磨成软件产品：欢迎落地页 + Cesium/ECharts 本地化 + Windows 一键脚本 + Docker 容器化 + 运行时健壮性 + 文档，新机器 `clone → setup → run` 即开即演。

**Architecture:** 前端资源本地化到 `web/`（消除 jsdelivr 依赖）→ 新增 `welcome.html` 落地页 → `setup.bat`/`run.bat`（Windows 演示）+ `Dockerfile`/`docker-compose.yml`（跨平台部署）→ `app.py` 运行时健壮性修复 → README 更新。

**Tech Stack:** Python 3.13、FastAPI/uvicorn、Cesium 1.95、ECharts 5.5、Docker（未装，构建验证待用户）、Windows bat。

## Global Constraints

- 运行环境：Python `D:\python.exe`，site-packages 在 `D:\Lib\site-packages`；所有用 rasterio 的脚本须在 `import rasterio` 前设 `os.environ["PROJ_LIB"]=r"D:\Lib\site-packages\rasterio\proj_data"`（及 `PROJ_DATA`）。
- 前端库本地化目标：`web/cesium/`（Cesium 1.95 Build/Cesium 全部 369 文件）与 `web/echarts.min.js`（ECharts 5.5）。
- 页面统一用 `/web/...` 相对服务根路径引用本地资源；**天地图 WMTS 底图保持在线**。
- 部署后新机无需 PostGIS：`app.py` 已具备数据库回退（读 `flood_out/` 本地文件）。
- Docker 本机未安装：Dockerfile/compose 只做**静态语法自查 + 清单核对**，镜像构建由用户在装有 Docker 的机器上执行。
- 数据大屏 `/api/monthly_rain` 依赖的 `precip_tif/` 未被提交 → 实现优雅回退，前端容错。

---

### Task 1: 前端资源本地化（Cesium + ECharts 下载并改页面引用）

**Files:**
- Create: `D:\Competiton\tools\download_web_libs.py`
- Create: `D:\Competiton\web\cesium\...`（369 文件，下载产物）
- Create: `D:\Competiton\web\echarts.min.js`
- Modify: `D:\Competiton\index.html`、`dashboard.html`、`flood.html`、`realevent.html`、`unet.html`（CDN 引用 → 本地）
- Test: `D:\Competiton\tests\test_local_resources.py`

**Interfaces:**
- Consumes: jsdelivr CDN（`cdn.jsdelivr.net`、`data.jsdelivr.com`）
- Produces: `web/cesium/`、`web/echarts.min.js`；页面 HTML 引用指向 `/web/...`

- [ ] **Step 1: 写下载脚本**

创建 `D:\Competiton\tools\download_web_libs.py`：

```python
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
```

- [ ] **Step 2: 运行下载脚本**

Run: `export PYTHONPATH=/d/Lib/site-packages && D:/python.exe tools/download_web_libs.py`
Expected: 逐个打印 `ok  web/cesium/...` 与 `web/echarts.min.js`，最后 `完成: 369 个 Cesium 文件 + ECharts`。网络中断可重跑（已下载的跳过）。

- [ ] **Step 3: 写本地资源测试**

创建 `D:\Competiton\tests\test_local_resources.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认失败（页面仍引用 CDN）**

Run: `D:/python.exe tests/test_local_resources.py`
Expected: `AssertionError: index.html 仍引用 jsdelivr`（尚未改页面）

- [ ] **Step 5: 修改 5 个页面的资源引用**

对 `index.html`、`flood.html`、`realevent.html`、`unet.html`：把
`https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Cesium.js` → `/web/cesium/Cesium.js`
`https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Widgets/widgets.css` → `/web/cesium/Widgets/widgets.css`

对 `dashboard.html`（及任何引用 echarts 的页面）：把
`https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js` → `/web/echarts.min.js`

可用一条批量命令核对替换覆盖（在每个文件上逐一 Edit，确保精确匹配）：
Run: `grep -rn "cdn.jsdelivr.net" index.html dashboard.html flood.html realevent.html unet.html`
Expected: 无输出（全部替换完成）

- [ ] **Step 6: 运行测试确认通过 + 服务验证**

Run: `D:/python.exe tests/test_local_resources.py`
Expected: `本地资源检查 OK / test_local_resources OK`

Run: 重启 uvicorn 后
`curl -s -o /dev/null -w "web/cesium/Cesium.js -> %{http_code}\n" http://127.0.0.1:8001/web/cesium/Cesium.js`
`curl -s -o /dev/null -w "web/echarts.min.js -> %{http_code}\n" http://127.0.0.1:8001/web/echarts.min.js`
Expected: 均 `200`。

- [ ] **Step 7: 提交**

```bash
cd /d/Competiton
git add tools/ web/ tests/test_local_resources.py index.html dashboard.html flood.html realevent.html unet.html
git commit -m "feat: 前端资源本地化(Cesium1.95+ECharts5.5, 消除jsdelivr CDN依赖)"
```

---

### Task 2: 欢迎落地页 `welcome.html` + 根路径重定向

**Files:**
- Create: `D:\Competiton\welcome.html`
- Modify: `D:\Competiton\app.py`（`root()` 重定向目标）
- Test: `D:\Competiton\tests\test_welcome.py`

**Interfaces:**
- Consumes: 无
- Produces: `GET /` → 302 → `/welcome.html`；`welcome.html` 含三个入口链接 `index.html` / `dashboard.html` / `realevent.html`

- [ ] **Step 1: 写测试**

创建 `D:\Competiton\tests\test_welcome.py`：

```python
# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_welcome_redirect_and_page():
    c = TestClient(app.app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code == 302 and "/welcome.html" in r.headers.get("location", ""), r.text[:120]
    p = c.get("/welcome.html")
    assert p.status_code == 200
    html = p.text
    for href in ["index.html", "dashboard.html", "realevent.html"]:
        assert href in html, "欢迎页缺入口 %s" % href
    print("welcome OK")

if __name__ == "__main__":
    test_welcome_redirect_and_page()
    print("test_welcome OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `export PYTHONPATH=/d/Lib/site-packages && D:/python.exe tests/test_welcome.py`
Expected: 失败（`/` 当前重定向到 `index.html` 且 `welcome.html` 不存在）

- [ ] **Step 3: 创建 `welcome.html`**

创建 `D:\Competiton\welcome.html`（深蓝科技风，与现有面板风格统一）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>基于 WebGIS 的三维城市降雨洪涝可视化</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Microsoft YaHei', sans-serif; min-height: 100vh;
      background: radial-gradient(1200px 600px at 50% -10%, rgba(79,195,247,0.18), transparent),
                  linear-gradient(160deg, #0a1428, #0d192f 55%, #07101f);
      color: #e6f1ff; display: flex; flex-direction: column; align-items: center; padding: 48px 24px; }
    .wrap { max-width: 920px; width: 100%; }
    .badge { display: inline-block; font-size: 12px; color: #4fc3f7; border: 1px solid rgba(79,195,247,0.4);
      padding: 4px 12px; border-radius: 999px; margin-bottom: 16px; }
    h1 { font-size: 30px; letter-spacing: 2px; color: #fff; margin-bottom: 10px; }
    .sub { color: #9fb3cc; font-size: 14px; margin-bottom: 34px; }
    .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .card { background: rgba(255,255,255,0.05); border: 1px solid rgba(79,195,247,0.25); border-radius: 14px;
      padding: 24px 20px; text-decoration: none; color: inherit; transition: all .18s; display: block; }
    .card:hover { background: rgba(79,195,247,0.12); border-color: #4fc3f7; transform: translateY(-3px); }
    .card .icon { font-size: 34px; margin-bottom: 10px; }
    .card h3 { font-size: 17px; margin-bottom: 8px; color: #4fc3f7; }
    .card p { font-size: 12px; color: #9fb3cc; line-height: 1.7; }
    .tech { margin-top: 34px; background: rgba(255,255,255,0.04); border: 1px solid rgba(79,195,247,0.18);
      border-radius: 14px; padding: 22px 24px; font-size: 13px; line-height: 1.9; color: #b9cbe0; }
    .tech b { color: #4fc3f7; }
    .tech .flow { margin: 10px 0; padding: 12px 14px; background: rgba(0,0,0,0.25); border-radius: 10px;
      font-family: Consolas, monospace; font-size: 12px; color: #8fe3ff; }
    .foot { margin-top: 30px; font-size: 12px; color: #6b8299; text-align: center; line-height: 1.8; }
  </style>
</head>
<body>
  <div class="wrap">
    <span class="badge">华南农业大学 · 2026 易智瑞杯 GIS 竞赛</span>
    <h1>基于 WebGIS 的三维城市降雨洪涝可视化</h1>
    <div class="sub">Vue3 + CesiumJS + UNet 深度学习 · 重现期情景模拟与真实事件反演</div>

    <div class="cards">
      <a class="card" href="index.html">
        <div class="icon">🌊</div>
        <h3>三维洪涝模拟</h3>
        <p>珠江新城三维场景，切换 2/5/10/50/100 年重现期，查看浴缸法反演的水位与水深。</p>
      </a>
      <a class="card" href="dashboard.html">
        <div class="icon">📊</div>
        <h3>数据大屏</h3>
        <p>KPI 指标、逐月降雨态势、水深分布与预警等级统计，一屏总览。</p>
      </a>
      <a class="card" href="realevent.html">
        <div class="icon">🧠</div>
        <h3>真实事件 · UNet 反演水深</h3>
        <p>2022-06 北江英德洪水真实卫星影像，经 UNet 提取淹没范围、水位反演得到三维水深。</p>
      </a>
    </div>

    <div class="tech">
      <b>技术路线</b>
      <div class="flow">降雨重现值(Gumbel) → 径流深 → 浴缸法反演水位 → 水深 = 水位 − 地形<br/>
        真实洪涝: Sentinel-1 RTC + Sentinel-2 光学 → 5波段 → UNet 水体提取 → 边界水位反演 → 水深</div>
      数据源：GLO-30 DEM · Sentinel-1/2 卫星影像 · PostGIS(可选) · 天地图底图。全部仅供教学演示，不作工程依据。
    </div>

    <div class="foot">
      运行入口：三维洪涝模拟(主场景，可切换"模拟/真实"模式) · 数据大屏 · 真实事件独立页<br/>
      建议使用 Chrome/Edge 打开；三维场景需联网加载天地图底图。
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 4: 修改 `app.py` 根路径重定向**

在 `app.py` 的 `root()` 中把重定向目标改为欢迎页：

```python
@app.get("/")
def root():
    return RedirectResponse("/welcome.html")
```

（当前是 `RedirectResponse("/index.html")`，直接改字符串即可。）

- [ ] **Step 5: 运行测试确认通过**

Run: `export PYTHONPATH=/d/Lib/site-packages && D:/python.exe tests/test_welcome.py`
Expected: `welcome OK / test_welcome OK`

Run: 重启 uvicorn 后 `curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" http://127.0.0.1:8001/`
Expected: `307 -> http://127.0.0.1:8001/welcome.html`（或 302，视 fastapi 版本；关键是 location 指向 welcome.html）

- [ ] **Step 6: 提交**

```bash
cd /d/Competiton
git add welcome.html app.py tests/test_welcome.py
git commit -m "feat: 欢迎落地页 + 根路径重定向"
```

---

### Task 3: 部署脚本（setup/run.bat + Docker + requirements 修订）

**Files:**
- Create: `D:\Competiton\setup.bat`
- Modify: `D:\Competiton\run.bat`
- Create: `D:\Competiton\Dockerfile`、`docker-compose.yml`、`.dockerignore`
- Modify: `D:\Competiton\requirements.txt`
- Test: `D:\Competiton\tests\test_deploy_files.py`

**Interfaces:**
- Consumes: `requirements.txt`、`app.py`
- Produces: 部署入口脚本与容器配置；`run.bat` 读取 `FLOOD_PORT` 环境变量（默认 8001）

- [ ] **Step 1: 写测试（静态清单核对）**

创建 `D:\Competiton\tests\test_deploy_files.py`：

```python
# -*- coding: utf-8 -*-
import os

ROOT = r"D:\Competiton"

def test_deploy_files():
    for f in ["setup.bat", "run.bat", "Dockerfile", "docker-compose.yml", ".dockerignore"]:
        assert os.path.exists(os.path.join(ROOT, f)), "缺部署文件 %s" % f
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "requests" in req, "requirements 缺 requests"
    assert "torchvision" not in req, "requirements 不应含 torchvision"
    run = open(os.path.join(ROOT, "run.bat"), encoding="utf-8").read()
    assert "FLOOD_PORT" in run, "run.bat 应支持 FLOOD_PORT"
    dk = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "uvicorn" in dk and "EXPOSE 8001" in dk, "Dockerfile 应含启动与端口"
    print("部署文件清单 OK")

if __name__ == "__main__":
    test_deploy_files()
    print("test_deploy_files OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `D:/python.exe tests/test_deploy_files.py`
Expected: `AssertionError: 缺部署文件 setup.bat`

- [ ] **Step 3: 创建 `setup.bat`**

```bat
@echo off
REM ============================================
REM  广东降雨洪涝 WebGIS - 一键安装依赖
REM  首次使用: 双击本文件; 之后直接运行 run.bat
REM ============================================
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] 检查 Python ...
where python >nul 2>nul || (echo [错误] 未找到 python, 请先安装 Python 3.10+ 并加入 PATH & pause & exit /b 1)
python -c "import sys; assert sys.version_info >= (3,10), '需 Python 3.10+'; print('    Python', sys.version.split()[0])" || (pause & exit /b 1)
echo [2/4] 创建虚拟环境 .venv ...
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
echo [3/4] 安装 Web/地理依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo [4/4] 安装 torch CPU 版 ...
pip install torch --index-url https://download.pytorch.org/whl/cpu
echo.
echo 安装完成! 请运行 run.bat 启动服务。
pause
```

- [ ] **Step 4: 修改 `run.bat`（支持 FLOOD_PORT + 自动开浏览器 + venv）**

将 `D:\Competiton\run.bat` 内容替换为：

```bat
@echo off
REM ============================================
REM  广东降雨洪涝 WebGIS - 一键启动
REM  依赖: 首次使用请先运行 setup.bat
REM ============================================
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo [提示] 未找到虚拟环境, 请先运行 setup.bat
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
if "%FLOOD_PORT%"=="" set FLOOD_PORT=8001
echo 启动服务 http://127.0.0.1:%FLOOD_PORT%/   (Ctrl+C 停止)
start "" "http://127.0.0.1:%FLOOD_PORT%/"
python -m uvicorn app:app --host 127.0.0.1 --port %FLOOD_PORT%
pause
```

- [ ] **Step 5: 创建 `Dockerfile`**

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# 先装依赖(利用镜像层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 拷贝应用(大文件由 .dockerignore 排除)
COPY . .

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 6: 创建 `docker-compose.yml`**

```yaml
services:
  webgis:
    build: .
    container_name: webgis-flood
    ports:
      - "8001:8001"
    environment:
      # PostGIS 可选; 不配置时服务层自动回退读本地 flood_out/ 文件
      - FLOOD_DB_HOST=host.docker.internal
      - FLOOD_DB_PORT=5432
      - FLOOD_DB_NAME=flood_analysis
      - FLOOD_DB_USER=postgres
      - FLOOD_DB_PASSWORD=123456
    restart: unless-stopped
```

- [ ] **Step 7: 创建 `.dockerignore`**

```
.git
.venv
__pycache__
*.pyc
tests
precip_tif
GF-FloodNet
NASA*
pre_*.nc
realevent_out/_cache.npz
*.docx
docx_media
```

- [ ] **Step 8: 修订 `requirements.txt`**

将 `D:\Competiton\requirements.txt` 替换为：

```
# ===== Web 服务 =====
fastapi
uvicorn[standard]
psycopg2-binary

# ===== 地理/栅格处理 =====
numpy
rasterio
tifffile
Pillow

# ===== 网络请求(真实事件卫星数据管线) =====
requests

# ===== 深度学习(CPU 版, 需单独用 pytorch cpu 源安装) =====
# pip install torch --index-url https://download.pytorch.org/whl/cpu
torch
```

- [ ] **Step 9: 运行测试确认通过**

Run: `D:/python.exe tests/test_deploy_files.py`
Expected: `部署文件清单 OK / test_deploy_files OK`

（可选）Run: `bash -n` 不适合 .bat；改为人工复核 bat 语法。Docker 文件语法：本机无 docker，留待用户在装 Docker 的机器上 `docker compose up -d` 验证。

- [ ] **Step 10: 提交**

```bash
cd /d/Competiton
git add setup.bat run.bat Dockerfile docker-compose.yml .dockerignore requirements.txt tests/test_deploy_files.py
git commit -m "feat: 部署脚本(Windows setup/run + Docker + requirements修订)"
```

---

### Task 4: 运行时健壮性（monthly_rain 回退 + 大屏容错）

**Files:**
- Modify: `D:\Competiton\app.py`（`/api/monthly_rain` 优雅回退）
- Modify: `D:\Competiton\dashboard.html`（空数据容错）
- Test: `D:\Competiton\tests\test_api_fallback.py`

**Interfaces:**
- Consumes: `precip_tif/precip_*.tif`（可能缺失）
- Produces: `/api/monthly_rain` 在数据缺失时返回 `{"years":[],"months":[],"monthly_rain":{}}` 而非 500；dashboard 显示"降雨数据缺失"占位

- [ ] **Step 1: 写测试**

创建 `D:\Competiton\tests\test_api_fallback.py`：

```python
# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_monthly_rain_fallback():
    c = TestClient(app.app)
    r = c.get("/api/monthly_rain")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    # precip_tif 可能缺失(未提交)或存在; 两种都应 200 且结构合法
    assert "years" in d and "months" in d and "monthly_rain" in d
    print("monthly_rain status=200, years=%d" % len(d["years"]))

if __name__ == "__main__":
    test_monthly_rain_fallback()
    print("test_api_fallback OK")
```

- [ ] **Step 2: 实现 `app.py` 优雅回退**

读取 `D:\Competiton\app.py` 中 `/api/monthly_rain`，若 `precip_tif/precip_<year>.tif` 不存在则整体返回空序列：

```python
@app.get("/api/monthly_rain")
def monthly_rain():
    """研究区 5 年逐月降雨(mm), 供数据大屏「降雨态势」图。precip_tif 未随仓库分发时优雅回退。"""
    years = [2021, 2022, 2023, 2024, 2025]
    out = {}
    for yr in years:
        p = os.path.join(ROOT, "precip_tif", "precip_%d.tif" % yr)
        if not os.path.exists(p):
            return {"years": [], "months": [], "monthly_rain": {},
                    "note": "precip_tif 数据未分发, 降雨态势不可用"}
        with rasterio.open(p) as src:
            w = from_bounds(113.30, 23.09, 113.34, 23.13, src.transform).round_offsets().round_lengths()
            d = src.read(window=w).astype("float32")
        d[d == -32768] = np.nan
        out[yr] = [round(float(np.nanmedian(d[b])) * 0.1, 1) for b in range(12)]
    return {"years": years, "months": list(range(1, 13)), "monthly_rain": out}
```

（用 Edit 在 `app.py` 的 `/api/monthly_rain` 函数体开头加缺失检查。）

- [ ] **Step 3: 大屏前端容错**

在 `D:\Competiton\dashboard.html` 的「降雨态势」块（当前 `var rainSeries = rainData.years.map(...)` 起，约 131-143 行）改为：当 `rainData.years` 为空时在 `cRain` 容器显示占位，不渲染 ECharts：

```html
      // 降雨态势(数据缺失时占位)
      if (rainData.years && rainData.years.length) {
        var rainSeries = rainData.years.map(function(y) {
          return { name: y + '年', type: 'line', smooth: true, symbol: 'none', data: rainData.monthly_rain[y] };
        });
        chart('cRain', {
          tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#9fb3cc' }, top: 0 },
          grid: { left: 42, right: 16, top: 34, bottom: 26 },
          xAxis: { type: 'category', data: rainData.months.map(function(m){return m+'月';}),
                   axisLabel: { color: '#7f9bb8' }, axisLine: { lineStyle: { color: '#2c4257' } } },
          yAxis: { type: 'value', name: 'mm', nameTextStyle: { color: '#7f9bb8' },
                   axisLabel: { color: '#7f9bb8' }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
          series: rainSeries
        });
      } else {
        document.getElementById('cRain').innerHTML =
          '<div style="color:#6b8299;text-align:center;padding-top:42px;font-size:13px;">降雨数据缺失(precip_tif 未分发)</div>';
      }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `export PYTHONPATH=/d/Lib/site-packages && D:/python.exe tests/test_api_fallback.py`
Expected: `monthly_rain status=200, years=0`（本机 precip_tif 存在则为 5；两种都过）`test_api_fallback OK`

Run: 重启 uvicorn 后 `curl -s http://127.0.0.1:8001/api/monthly_rain | head -c 120`
Expected: 200 JSON。

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add app.py dashboard.html tests/test_api_fallback.py
git commit -m "feat: monthly_rain 优雅回退 + 大屏空数据容错"
```

---

### Task 5: README 更新 + 最终验证

**Files:**
- Modify: `D:\Competiton\README.md`

- [ ] **Step 1: 重写 `README.md`**

将 `D:\Competiton\README.md` 重写为含以下小节（保留既有架构/算法/API 内容，新增部署与演示）：

```markdown
# 基于 WebGIS 的三维城市降雨洪涝可视化

华南农业大学 · 2026 易智瑞杯 GIS 竞赛（C-GIS 组）参赛作品。Vue3 + CesiumJS + UNet 深度学习，
实现不同重现期降雨条件下城市积水深度与淹没范围的三维动态表达，并支持真实洪涝事件（北江 2022-06 英德）
卫星影像 → UNet 水体提取 → 水位反演 → 三维水深。

## 快速开始

### 方式一：Windows 一键脚本（推荐演示）
1. `git clone <仓库地址>` 并进入目录
2. 双击 `setup.bat`（创建虚拟环境并安装依赖，含 torch CPU 版）
3. 双击 `run.bat`（启动服务并自动打开浏览器 → 欢迎页）

### 方式二：Docker
```bash
docker compose up -d
# 打开 http://localhost:8001/
```
> 镜像约 2-3GB（含 torch CPU）。PostGIS 可选：不配置时服务层自动回退读本地文件。

## 系统架构
（保留原有架构图）

## 演示流程
欢迎页 → 三维洪涝模拟（主场景，切换「模拟·珠江新城 / 真实·英德」）→ 数据大屏 → 真实事件独立页

## 算法与数据管线
（保留原有算法说明，补充真实事件管线）

## API 接口
（保留原 API 表，补充 /api/realevent）

## 常见问题
- 三维场景白屏/无底图：需联网加载天地图底图与本地化 Cesium。
- 数据大屏降雨图为空：precip_tif 未随仓库分发（体积大），属预期；其余功能不受影响。
- torch 安装失败：手动执行
  `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- 需要 PostGIS：配置 .env 环境变量后重启；服务层优先读库、失败自动回退文件。
```

> 实现时保留原 README 的架构图、目录结构、算法说明、API 表等有效内容，按上述结构重排。

- [ ] **Step 2: 最终验证**

- [ ] 运行全部测试：`for t in tests/test_*.py; do D:/python.exe $t; done` → 全部 `OK`
- [ ] 重启 uvicorn 后逐一 curl：`/`（302→welcome）、`/welcome.html`、`/index.html`、`/web/cesium/Cesium.js`、`/web/echarts.min.js`、`/api/realevent`、`/api/monthly_rain` → 均 200
- [ ] `grep -rn "cdn.jsdelivr.net\|unpkg.com" *.html` → 无输出（无 CDN 依赖）
- [ ] 浏览器打开 `http://127.0.0.1:8001/` 人工核验欢迎页与三入口

- [ ] **Step 3: 提交**

```bash
cd /d/Competiton
git add README.md
git commit -m "docs: README 更新(部署/演示/常见问题)"
```

---

## 验证（最终）

- [ ] 本机（Windows）：测试全过；服务重启后所有关键 URL 200；无 CDN 引用；浏览器欢迎页正常。
- [ ] Docker：本机未装 Docker → 由用户在装 Docker 的机器执行 `docker compose up -d` 验证（清单已就绪）。
