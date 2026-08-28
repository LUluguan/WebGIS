# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
