# -*- coding: utf-8 -*-
"""部署文件 + 交付副本守护(2026-10 审计 QA-01/MISS-01/02/03):
- 部署文件清单完整;
- 交付副本(C2132_开发源码)能 import、无缺失本地依赖、无凭据文件、能出数(/api/scenarios 200);
- 交付 zip 不落后于仓库(防止旧包随交)。
副本不存在时一律 pytest.skip(skipped 计数显式可见, 不再与"通过"不可区分)。"""
import os, sys, importlib.util
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 凭据/运行数据黑名单(与 tools/sync_delivery.py DENY 一致)
DENY = [os.path.join("web", "config.local.js"), ".env", "web_users.json"]


def test_deploy_files():
    for f in ["setup.bat", "run.bat", "Dockerfile", "docker-compose.yml", ".dockerignore"]:
        assert os.path.exists(os.path.join(ROOT, f)), "缺部署文件 %s" % f
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "requests" in req, "requirements 缺 requests"
    assert "torchvision" not in req, "requirements 不应含 torchvision"
    # python-multipart: UploadFile 路由注册的硬依赖, 缺失则 app 导入即崩(2026-10 审计 P0)
    assert "python-multipart" in req, "requirements 缺 python-multipart"
    if importlib.util.find_spec("multipart") is None and importlib.util.find_spec("python_multipart") is None:
        print("WARN: 本机未安装 python-multipart(requirements 已声明, setup 时会装上)")
    run = open(os.path.join(ROOT, "run.bat"), encoding="utf-8").read()
    assert "FLOOD_PORT" in run, "run.bat 应支持 FLOOD_PORT"
    dk = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "uvicorn" in dk and "EXPOSE 8001" in dk, "Dockerfile 应含启动与端口"
    assert "fonts-wqy-microhei" in dk, "Dockerfile 应安装中文字体(专题图制图需要)"
    print("部署文件清单 OK(含 python-multipart 与中文字体)")


def _copy_dir():
    d = os.path.join(ROOT, "C2132_开发源码")
    if not os.path.isdir(d):
        pytest.skip("交付副本不存在(CI 由 tools/sync_delivery.py 先行生成)")
    return d


def test_delivery_copy_importable():
    """交付副本必须能独立 import app(2026-10 审计 P0: store.py 漏同步)。"""
    import subprocess
    copy_dir = _copy_dir()
    r = subprocess.run([sys.executable, "-c", "import app"],
                       capture_output=True, cwd=copy_dir,
                       env=dict(os.environ, PYTHONPATH=copy_dir))
    assert r.returncode == 0, "交付副本 import app 失败: %s" % r.stderr.decode("utf-8", "replace")[:200]
    print("交付副本 import app OK")


def test_delivery_copy_no_missing_local_imports():
    """解析 app.py 的 from X import / import X 本地模块名, 断言副本里 X.py 存在。"""
    copy_dir = _copy_dir()
    copy_app = os.path.join(copy_dir, "app.py")
    src = open(copy_app, encoding="utf-8").read()
    local = set()
    for m in __import__("re").finditer(r"from (\w+) import|import (\w+)", src):
        mod = m.group(1) or m.group(2)
        if os.path.exists(os.path.join(ROOT, mod + ".py")):
            local.add(mod)
    for mod in sorted(local):
        assert os.path.exists(os.path.join(copy_dir, mod + ".py")), \
            "交付副本缺 %s.py (app.py 依赖它)" % mod
    print("本地依赖完整性 OK:", sorted(local))


def test_delivery_copy_no_credentials():
    """MISS-01(2026-10 审计 DEL-01): 副本内不得存在凭据/隐私文件。
    web/config.local.js(天地图 key + Ion token)曾被手工 cp 带入并随包交付。"""
    copy_dir = _copy_dir()
    for rel in DENY:
        p = os.path.join(copy_dir, rel)
        assert not os.path.exists(p), "交付副本含凭据文件 %s — 运行 tools/sync_delivery.py 清除" % rel
    print("交付副本凭据黑名单 OK:", DENY)


def test_delivery_copy_serves_data():
    """MISS-02(2026-10 审计 DEL-02): 副本必须能出数而不只 import——
    旧版 10 个核心端点 6 个 404(数据在平级的 C2132_相关数据 目录, 未随代码走)。"""
    import subprocess
    copy_dir = _copy_dir()
    if not os.path.exists(os.path.join(ROOT, "flood_out", "scenarios.json")):
        pytest.skip("根目录无情景数据(未同步), 副本出数无从验证")
    code = ("import app;from fastapi.testclient import TestClient;"
            "c=TestClient(app.app);r=c.get('/api/scenarios');print(r.status_code)")
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, cwd=copy_dir,
                       env=dict(os.environ, PYTHONPATH=copy_dir))
    assert r.returncode == 0, "副本内起服务失败: %s" % r.stderr.decode("utf-8", "replace")[:300]
    assert r.stdout.strip().endswith(b"200"), \
        "交付副本缺运行数据(/api/scenarios=%s): 先运行 tools/sync_delivery.py" % \
        r.stdout.strip().decode("utf-8", "replace")
    print("交付副本 /api/scenarios 200 OK(可独立出数)")


def test_delivery_copy_tracked_complete():
    """DEL-04 级守护(清单无关): git 跟踪的每一个文件(除运行期 reports/)都必须存在于副本。
    flood_depth_*y.png 曾漏出 FILES 清单 → 交付版 flood.html 水深色带图层全 404;
    web/echarts.min.js 曾不在任何清单 → CI 全新副本里 analysis/dashboard 页面断链。
    以 git 跟踪清单为真源, 新布局(pipeline/ web/data/ deploy/ docs/)自动覆盖。"""
    import subprocess
    copy_dir = _copy_dir()
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        pytest.skip("非 git 环境, 跳过副本完整性守护")
    missing = []
    checked = 0
    for rel in r.stdout.splitlines():
        rp = rel.replace("\\", "/")
        if rp.startswith("reports/"):   # 运行期用户数据, 按隐私策略不入副本
            continue
        checked += 1
        if not os.path.exists(os.path.join(copy_dir, rp)):
            missing.append(rp)
    assert not missing, "交付副本缺文件(先运行 tools/sync_delivery.py): %s" % missing[:12]
    print("交付副本 tracked 完整性 OK(%d 项核查)" % checked)


def test_delivery_zip_not_stale():
    """MISS-03(2026-10 审计 DEL-03): 作品 zip 的修改时间必须晚于最后一次提交。
    旧版 zip 落后 7 周且为分卷包(Windows 资源管理器双击必失败), 防止旧包随交。"""
    zp = os.path.join(ROOT, "C2132_作品.zip")
    if not os.path.exists(zp):
        pytest.skip("C2132_作品.zip 不存在(提交前需重打单一 zip)")
    import subprocess
    r = subprocess.run(["git", "log", "-1", "--format=%ct"], capture_output=True,
                       cwd=ROOT)
    if r.returncode != 0:
        pytest.skip("非 git 环境, 跳过 zip 新鲜度检查")
    head_ts = int(r.stdout.strip() or 0)
    zip_ts = int(os.path.getmtime(zp))
    assert zip_ts >= head_ts, (
        "C2132_作品.zip 落后于仓库(打包于 %s, 最后提交于 %s)——"
        "提交前必须重打包(单一 zip, 不含 web/config.local.js 与 web_users.json, 附数据摆放说明)" % (
            __import__("datetime").datetime.fromtimestamp(zip_ts).strftime("%Y-%m-%d %H:%M"),
            __import__("datetime").datetime.fromtimestamp(head_ts).strftime("%Y-%m-%d %H:%M")))


if __name__ == "__main__":
    test_deploy_files()
    test_delivery_copy_importable()
    test_delivery_copy_no_missing_local_imports()
    test_delivery_copy_no_credentials()
    test_delivery_copy_serves_data()
    test_delivery_copy_tracked_complete()
    test_delivery_zip_not_stale()
    print("test_deploy_files OK")
