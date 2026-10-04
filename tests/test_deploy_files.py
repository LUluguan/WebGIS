# -*- coding: utf-8 -*-
import os, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

if __name__ == "__main__":
    test_deploy_files()
    print("test_deploy_files OK")
