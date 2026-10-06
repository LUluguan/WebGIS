# -*- coding: utf-8 -*-
"""sync_delivery.py — 仓库 → C2132_开发源码 交付副本单向同步(md5 守卫, 幂等)。
交付副本必须由本脚本维护, 禁止手工 cp(2026-10 审计: 手工 cp 造成副本落后 6 行)。
同步集 = 静态清单 ∪ git 跟踪的全部文件(除 reports/ 运行数据; DEL-04 后不再依赖
手维护清单防遗漏) ∪ web/data 生成数据; tests/tools/docs/web 整目录与四类数据目录随包。
副本镜像仓库新布局(pipeline/ web/data/ deploy/ docs/), 使其可独立起服务。"""
import glob, hashlib, os, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
SRC = os.path.join(ROOT, "C2132_开发源码")

# 静态清单: 未提交的新文件(git 跟踪不到)也必须随包; 已跟踪文件由下方并集兜底
FILES = [
    "app.py", "pipeline_config.py", "proj_fix.py", "store.py", "flood_render.py",
    "sat_data.py", "sar_change.py", "unet_model.py", "unet_apply.py",
    os.path.join("pipeline", "bathtub_flood.py"), os.path.join("pipeline", "fetch_dem.py"),
    os.path.join("pipeline", "fetch_pop.py"), os.path.join("pipeline", "prep_precip.py"),
    os.path.join("pipeline", "prep_return_period.py"),
    os.path.join("pipeline", "prep_design_storm.py"),
    os.path.join("pipeline", "water_level_inversion.py"),
    os.path.join("pipeline", "load_flood_pg.py"), os.path.join("pipeline", "export_web.py"),
    os.path.join("pipeline", "export_dashboard.py"),
    os.path.join("pipeline", "export_unet_demo.py"),
    os.path.join("pipeline", "realevent_beijiang.py"), os.path.join("pipeline", "train_unet.py"),
    os.path.join("pipeline", "eval_unet.py"), os.path.join("pipeline", "infer_unet.py"),
    "index.html", "welcome.html", "dashboard.html", "realevent.html",
    "unet.html", "flood.html", "analysis.html", "compare.html",
    "mobile.html", "login.html", "thematic.html",
    "realevent_events.json", "gz_tower_buildings.geojson",
    "requirements.txt", "Dockerfile", "docker-compose.yml", ".dockerignore",
    ".gitignore", ".gitattributes", "pytest.ini", "README.md",
    "setup.bat", "run.bat", ".env.example",
    os.path.join("deploy", "watchdog.bat"), os.path.join("deploy", "load_buildings.sql"),
    os.path.join("web", "common.js"), os.path.join("web", "config.example.js"),
    os.path.join(".github", "workflows", "ci.yml"),
]

# 凭据/运行数据黑名单: 即使被手工 cp 带入也强制移除(2026-10 审计 DEL-01:
# web/config.local.js 曾被手工拷入副本, 打包后天地图 key/Ion token 直接交给组委会)
DENY = [
    os.path.join("web", "config.local.js"),   # 天地图 key + Cesium Ion token
    ".env",                                    # 数据库口令等环境配置
    "web_users.json",                          # 用户口令哈希
]
_DENY_SLASH = {d.replace("\\", "/") for d in DENY}

# git 跟踪全集并入(清单无关, 防再漏); 仅排除运行期用户数据目录
_TRACKED_EXCLUDE = ("reports/",)
try:
    _g = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                        cwd=ROOT, timeout=30).stdout.splitlines()
    _tracked = [x.replace("\\", "/") for x in _g
                if x and not x.startswith(_TRACKED_EXCLUDE)]
except Exception:
    _tracked = []
FILES = sorted(set(FILES) | set(_tracked)
               | {os.path.join("web", "data", os.path.basename(p))
                  for p in glob.glob("web/data/flood_depth_*y.png")}
               - set(DENY))

# 运行数据(存在才同步; 使副本 /api/scenarios 等端点可出数, 供守护测试与评审直接运行)
# _cache.npz 为 STAC 下载缓存(管线重启加速用), 服务不需要且体积大 → 不随包
DATA_DIRS = {
    "dem": (".tif",),
    "flood_out": (".tif", ".json", ".geojson"),
    "unet_out": (".pt", ".json", ".png"),
    "realevent_out": (".tif", ".json", ".png"),
}
DENY_SUFFIX = (".npz",)

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

copied = same = miss = 0
for rel in FILES:
    r = os.path.join(ROOT, rel)
    if not os.path.isfile(r):
        print("MISS(root)", rel)
        miss += 1
        continue
    d = os.path.join(SRC, rel)
    os.makedirs(os.path.dirname(d) or SRC, exist_ok=True)
    if os.path.exists(d) and md5(r) == md5(d):
        same += 1
        continue
    shutil.copy2(r, d)
    copied += 1
    print("copied", rel)

for sub in ("tests", "tools", "docs", "web"):
    base = os.path.join(ROOT, sub)
    for dp, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if x != "__pycache__"]
        for name in fn:
            r = os.path.join(dp, name)
            rel = os.path.relpath(r, ROOT).replace("\\", "/")
            if rel in _DENY_SLASH or ".tmp_read" in r:
                continue
            d = os.path.join(SRC, rel)
            os.makedirs(os.path.dirname(d) or SRC, exist_ok=True)
            if os.path.exists(d) and md5(r) == md5(d):
                same += 1
                continue
            shutil.copy2(r, d)
            copied += 1
            print("copied", rel)

# ==== 运行数据同步(存在才拷; 无 .npz 缓存) ====
data_copied = 0
for sub, exts in DATA_DIRS.items():
    base = os.path.join(ROOT, sub)
    if not os.path.isdir(base):
        continue
    for dp, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if x != "__pycache__"]
        for name in fn:
            if not name.lower().endswith(exts) or name.lower().endswith(DENY_SUFFIX):
                continue
            r = os.path.join(dp, name)
            rel = os.path.relpath(r, ROOT)
            d = os.path.join(SRC, rel)
            os.makedirs(os.path.dirname(d) or SRC, exist_ok=True)
            if os.path.exists(d) and md5(r) == md5(d):
                same += 1
                continue
            shutil.copy2(r, d)
            data_copied += 1
            copied += 1
            print("copied[data]", rel)
if data_copied == 0:
    print("data: all identical (or no data dirs present)")

# ==== 凭据/运行数据黑名单: 强制移除(含手工 cp 历史遗留) ====
for rel in DENY:
    p = os.path.join(SRC, rel)
    if os.path.isfile(p):
        os.remove(p)
        print("DENIED-REMOVED", rel)
# config.local.js 的合法来源只有 config.example.js 模板: 副本里缺 key 时底图走兜底
if not os.path.isfile(os.path.join(SRC, "web", "config.example.js")):
    print("WARN: 副本缺 web/config.example.js(部署说明模板)")

print("SYNC done: copied %d, identical %d, missing-in-root %d" % (copied, same, miss))
