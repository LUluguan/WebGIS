# -*- coding: utf-8 -*-
"""store.py — JSON 持久化 store(reports/subscribers/users) + 频控 + 原子写 + 文本消毒。
从 app.py 切出(2026-10 结构债), 边界: 纯文件 I/O + 内存锁, 不依赖 FastAPI/栅格。"""
import collections
import datetime
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid

_log = logging.getLogger("flood.store")
ROOT = os.path.dirname(os.path.abspath(__file__))

# ==== 报讯 store ====
_REPORT_DIR = os.path.join(ROOT, "reports")
_REPORTS_FILE = os.path.join(_REPORT_DIR, "reports.json")
_reports_degraded_from = None   # 隔离现场路径(非 None 时拒绝覆盖写)
_report_lock = threading.Lock()
_report_rl = {}                 # ip -> deque: 频控表

# ==== 订阅 store ====
_SUBSCRIBERS_FILE = os.path.join(_REPORT_DIR, "subscribers.json")

# ==== 用户 store ====
_AUTH_FILE = os.path.join(ROOT, "web_users.json")

_TRUST_PROXY = os.environ.get("FLOOD_TRUST_PROXY", "").strip() == "1"


def _atomic_write_json(path, data):
    """统一原子 JSON 写入(tmp + os.replace), 三个 store 共用。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _client_ip(request):
    """客户端 IP: 默认取 socket 对端(恶意客户端可伪造 XFF 绕过频控);
    仅当 FLOOD_TRUST_PROXY=1(部署在可信反代之后)时才取 X-Forwarded-For 首值。"""
    if _TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_rate_lock = threading.Lock()   # check-then-act 需原子(2026-10 审计 REL-02: 此前声明未接线)


def _rate_ok(key, limit=20, window=60.0):
    """滑动窗口频控: window 秒内同一 key 最多 limit 次。
    key 除 IP 外可带业务前缀与用户名(login:/sub:), 各接口互不干扰。
    check-then-act 全程持 _rate_lock: 并发下不会越过 limit(GIL 之外的显式保证)。"""
    with _rate_lock:
        dq = _report_rl.setdefault(key, collections.deque())
        now = time.time()
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


# 订阅表读-改-写锁(与报讯锁分开, 避免互不相关的写盘互相等待)
_subscribers_lock = threading.Lock()

SUBSCRIBER_CAP = 500   # 订阅总量上限: 灌库防护(2026-10 审计 SEC-04)


def _clean_text(v, maxlen):
    """用户自由文本: 必须是字符串, 剥除 <...> 标签片段(存储型 XSS 加固), 截断。"""
    if not isinstance(v, str):
        raise ValueError("文本字段必须为字符串")
    v = re.sub(r"<[^>]*>", "", v)
    return v.strip()[:maxlen]


# ==== 报讯 store 读写 ====
def _reports_file():
    return _REPORTS_FILE


_reports_degraded_from = None


def _load_reports():
    """报讯 store 读取; 损坏文件隔离(带时间戳)并回退空表; 降级期间拒绝覆盖写。"""
    global _reports_degraded_from
    p = _reports_file()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                v = json.load(f)
            if not isinstance(v, list):
                raise ValueError("顶层不是数组(类型 %s)" % type(v).__name__)
            return v
        except Exception as e:
            q = p + ".corrupt-%s" % datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            try:
                os.replace(p, q)
                _reports_degraded_from = q
            except OSError as e2:
                _log.warning("reports.json 损坏隔离失败(将按空表运行): %s", e2)
            _state_quarantines[0] += 1
            _log.warning("reports.json 解析失败(撕裂/损坏), 已隔离到 %s: %s", q or "(隔离失败)", e)
    return []


_state_quarantines = [0]   # 可变容器以便 store 内部自增, app.py /health 读取


def _save_reports(lst):
    """写入前检查降级状态: 存在隔离现场时先合并历史, 合并失败则拒绝覆盖。"""
    global _reports_degraded_from
    if _reports_degraded_from and os.path.exists(_reports_degraded_from):
        try:
            with open(_reports_degraded_from, encoding="utf-8") as f:
                old = json.load(f)
            known = {x["id"] for x in lst}
            merged = [x for x in old if x.get("id") not in known] + lst
            _atomic_write_json(_reports_file(), merged)
            _log.warning("已从隔离件恢复 %d 条历史报讯并合并写入", len(old))
            _reports_degraded_from = None
            return
        except Exception as e:
            _log.exception("报讯存储降级期间拒绝覆盖写入(历史保留于 %s): %s",
                           _reports_degraded_from, e)
            raise RuntimeError("报讯存储受损, 已保护现场, 拒绝覆盖写入")
    _atomic_write_json(_reports_file(), lst)


# ==== 订阅 store ====
def _load_subscribers():
    if os.path.exists(_SUBSCRIBERS_FILE):
        try:
            v = json.load(open(_SUBSCRIBERS_FILE, encoding="utf-8"))
            if isinstance(v, list):
                return v
            _log.warning("subscribers.json 内容不是列表(类型 %s), 按空表处理", type(v).__name__)
        except Exception as e:
            _log.warning("subscribers.json 解析失败, 按空表处理: %s", e)
    return []


def _save_subscribers(lst):
    _atomic_write_json(_SUBSCRIBERS_FILE, lst)


# ==== 用户 store ====
def _hash_pw(pw, salt):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def _load_users():
    """用户表缺失时自动播种演示账号。"""
    if not os.path.exists(_AUTH_FILE):
        users = []
        for uname, pw, role in (("admin", "admin123", "admin"), ("public", "123456", "public")):
            salt = uuid.uuid4().hex[:12]
            users.append({"username": uname, "salt": salt,
                          "hash": _hash_pw(pw, salt), "role": role})
        _atomic_write_json(_AUTH_FILE, users)
        return users
    try:
        with open(_AUTH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log.warning("web_users.json 解析失败: %s", e)
        return []
