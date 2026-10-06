# -*- coding: utf-8 -*-
"""所有测试的 JSON store(web_users/reports/subscribers)重定向到临时目录。
2026-10 审计: 任何人跑测试都可能污染交付数据(reports.json 曾被灌到 57 条)。
autouse + monkeypatch → 测试结束自动丢弃, 交付目录零接触。"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    import app
    import store
    d = tmp_path / "stores"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_REPORT_DIR", str(d))
    monkeypatch.setattr(store, "_REPORTS_FILE", str(d / "reports.json"))
    monkeypatch.setattr(store, "_SUBSCRIBERS_FILE", str(d / "subscribers.json"))
    monkeypatch.setattr(store, "_AUTH_FILE", str(d / "web_users.json"))
    monkeypatch.setattr(app, "_tokens", {})
    monkeypatch.setattr(app, "_REPORT_DIR", str(d))
    monkeypatch.setattr(store, "_report_rl", {})   # 频控表逐用例隔离(登录/订阅/报讯互不串扰)
    yield
