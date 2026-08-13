# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_welcome_redirect_and_page():
    c = TestClient(app.app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code in (302, 307) and "/welcome.html" in r.headers.get("location", ""), r.text[:120]
    p = c.get("/welcome.html")
    assert p.status_code == 200
    html = p.text
    for href in ["index.html", "dashboard.html", "realevent.html"]:
        assert href in html, "欢迎页缺入口 %s" % href
    print("welcome OK")

if __name__ == "__main__":
    test_welcome_redirect_and_page()
    print("test_welcome OK")
