# api/_core.py — subman-web 后端公共逻辑(不直接对外暴露)
import base64
import json
import os
import re
import secrets
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _airportlib as ap  # noqa: E402  (vendored, 仅用标准库)

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
KV_URL = os.environ.get("KV_REST_API_URL", "")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")
LOCAL_DIR = os.environ.get("SUBMAN_LOCAL_DIR", "")  # 本地测试用: 文件后端目录

STORE_KEY = "subman:store"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CLASH_RE = re.compile(r"^\s*proxies\s*:", re.M)


# ---------- 存储(KV REST / 本地文件) ----------

def _kv_rest(method, parts, body=None):
    url = KV_URL.rstrip("/") + "/" + "/".join(
        urllib.parse.quote(p, safe="") for p in parts)
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Authorization": f"Bearer {KV_TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def kv_get(key):
    if KV_URL:
        try:
            res = _kv_rest("GET", ["get", key])
        except Exception:
            return None
        return res.get("result")
    if LOCAL_DIR:
        p = os.path.join(LOCAL_DIR, "kv_" + re.sub(r"\W", "_", key))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    return None


def kv_set(key, value):
    if KV_URL:
        _kv_rest("POST", ["set", key], value.encode("utf-8"))
        return
    if LOCAL_DIR:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        p = os.path.join(LOCAL_DIR, "kv_" + re.sub(r"\W", "_", key))
        with open(p, "w", encoding="utf-8") as f:
            f.write(value)


def kv_del(key):
    if KV_URL:
        try:
            _kv_rest("GET", ["del", key])
        except Exception:
            pass
        return
    if LOCAL_DIR:
        p = os.path.join(LOCAL_DIR, "kv_" + re.sub(r"\W", "_", key))
        if os.path.exists(p):
            os.remove(p)


def new_token():
    return secrets.token_urlsafe(24)


def default_store():
    return {"subs": {},
            "bundle": {"token": new_token(), "subs": [],
                       "no_dedup": False, "updated": None}}


def load_store():
    raw = kv_get(STORE_KEY)
    if raw:
        try:
            s = json.loads(raw)
            s.setdefault("subs", {})
            s.setdefault("bundle", default_store()["bundle"])
            return s
        except Exception:
            pass
    return default_store()


def save_store(s):
    kv_set(STORE_KEY, json.dumps(s, ensure_ascii=False))


def cache_key(name):
    return f"subman:cache:{name}"


# ---------- 认证 ----------

def check_auth(headers):
    if not ADMIN_USER or not ADMIN_PASS:
        return False
    auth = headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        u, p = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
    except Exception:
        return False
    return u == ADMIN_USER and p == ADMIN_PASS


# ---------- 订阅管理 ----------

def valid_name(name):
    return bool(NAME_RE.match(name or ""))


def add_url_sub(store, name, url):
    if not valid_name(name):
        return "订阅名只允许字母/数字/_/-"
    if name in store["subs"]:
        return f"已存在名为 {name} 的订阅"
    if not re.match(r"^https?://", url or "", re.I):
        return "URL 须以 http(s):// 开头"
    now = _now()
    store["subs"][name] = {"type": "url", "url": url, "kind": "uri",
                           "added": now, "fetched": None, "count": None}
    return None


def add_inline_sub(store, name, text):
    if not valid_name(name):
        return "订阅名只允许字母/数字/_/-"
    if name in store["subs"]:
        return f"已存在名为 {name} 的订阅"
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and "://" in l]
    if not lines:
        return "没有读到节点链接"
    now = _now()
    store["subs"][name] = {"type": "inline", "nodes": lines,
                           "added": now, "count": len(lines)}
    return None


def remove_sub(store, name):
    if name not in store["subs"]:
        return f"没有名为 {name} 的订阅"
    del store["subs"][name]
    kv_del(cache_key(name))
    b = store["bundle"]
    if name in b["subs"]:
        b["subs"] = [n for n in b["subs"] if n != name]
    return None


def fetch_sub(store, name):
    sub = store["subs"].get(name)
    if not sub:
        return "没有名为 %s 的订阅" % name
    if sub["type"] != "url":
        return f"{name} 是内联订阅, 无需拉取"
    try:
        raw, _desc = ap.fetch_source(sub["url"])
    except Exception as e:
        return f"{name}: 拉取失败 {e}"
    if CLASH_RE.search(ap.normalize(raw)):
        sub["kind"] = "clash"
        sub["fetched"] = _now()
        sub["count"] = 0
        return f"{name}: 该订阅是 Clash YAML 格式, 网页面板暂不支持(可用本地 substore.py 转换)"
    nodes, _un = ap.parse_subscription(raw)
    kv_set(cache_key(name), raw if raw.endswith("\n") else raw + "\n")
    sub["kind"] = "uri"
    sub["fetched"] = _now()
    sub["count"] = len(nodes)
    return None


def _now():
    import time
    return int(time.time())


# ---------- 合成订阅 ----------

def build_bundle(store, allow_live_fetch=True):
    """返回 (base64文本, 节点数, 无法解析行数, [(订阅名, 行数, 备注)])."""
    b = store["bundle"]
    names = b["subs"] or sorted(store["subs"].keys())
    chunks, info = [], []
    for name in names:
        sub = store["subs"].get(name)
        if not sub:
            continue
        if sub["type"] == "inline":
            ls = sub["nodes"]
            chunks.append("\n".join(ls))
            info.append((name, len(ls), None))
            continue
        if sub.get("kind") == "clash":
            info.append((name, 0, "Clash 格式暂不支持, 已跳过"))
            continue
        raw = kv_get(cache_key(name))
        if raw is None and allow_live_fetch:
            try:
                raw, _ = ap.fetch_source(sub["url"])
                kv_set(cache_key(name), raw if raw.endswith("\n") else raw + "\n")
            except Exception as e:
                info.append((name, 0, f"拉取失败: {e}"))
                continue
        if raw is None:
            info.append((name, 0, "无缓存, 请先点更新"))
            continue
        norm = ap.normalize(raw)
        ls = [l for l in norm.splitlines() if l.strip()]
        chunks.append("\n".join(ls))
        info.append((name, len(ls), None))
    text = "\n".join(chunks)
    nodes, unknown = ap.parse_subscription(text, dedup=not b["no_dedup"])
    out = base64.b64encode(
        ("\n".join(n["raw"] for n in nodes) + "\n").encode("utf-8")).decode()
    return out, len(nodes), unknown, info


def public_link(host, token):
    return f"https://{host}/s/{token}"
