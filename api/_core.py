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
import _rename as rn  # noqa: E402

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
                       "no_dedup": False, "flags": True, "ip_filter": "all",
                       "updated": None}}


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


def add_inline_sub(store, name, text, rename_prefix="", rename_start=1,
                   auto_rename=False):
    if not valid_name(name):
        return "订阅名只允许字母/数字/_/-"
    if name in store["subs"]:
        return f"已存在名为 {name} 的订阅"
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and "://" in l]
    if not lines:
        return "没有读到节点链接"
    prefix = (rename_prefix or "").strip()
    if prefix:
        try:
            start = int(rename_start or 1)
        except Exception:
            start = 1
        renamed = []
        for i, ln in enumerate(lines, start):
            n = ap.parse_full(ln)
            renamed.append(rn.rename_node(n, f"{prefix}{i}") if n else ln)
        lines = renamed
    elif auto_rename:
        lines = auto_rename_nodes(store, lines)
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


# ---------- 地区旗帜(GeoIP) ----------

FLAG_RE = re.compile(r"^(?:[\U0001F1E6-\U0001F1FF]{2}\s*)+")

COUNTRY_ZH = {
    "HK": "香港", "TW": "台湾", "MO": "澳门", "CN": "中国",
    "US": "美国", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西",
    "AR": "阿根廷", "CL": "智利", "CO": "哥伦比亚", "PE": "秘鲁",
    "GB": "英国", "IE": "爱尔兰", "FR": "法国", "DE": "德国",
    "NL": "荷兰", "BE": "比利时", "LU": "卢森堡", "CH": "瑞士",
    "AT": "奥地利", "ES": "西班牙", "PT": "葡萄牙", "IT": "意大利",
    "SE": "瑞典", "NO": "挪威", "DK": "丹麦", "FI": "芬兰",
    "IS": "冰岛", "PL": "波兰", "CZ": "捷克", "SK": "斯洛伐克",
    "HU": "匈牙利", "RO": "罗马尼亚", "BG": "保加利亚", "GR": "希腊",
    "HR": "克罗地亚", "SI": "斯洛文尼亚", "EE": "爱沙尼亚",
    "LV": "拉脱维亚", "LT": "立陶宛", "UA": "乌克兰", "TR": "土耳其",
    "RU": "俄罗斯", "KZ": "哈萨克斯坦", "IL": "以色列", "AE": "阿联酋",
    "SA": "沙特", "QA": "卡塔尔",
    "JP": "日本", "KR": "韩国", "MN": "蒙古",
    "SG": "新加坡", "MY": "马来西亚", "TH": "泰国", "VN": "越南",
    "PH": "菲律宾", "ID": "印度尼西亚", "KH": "柬埔寨", "LA": "老挝",
    "MM": "缅甸", "BD": "孟加拉", "IN": "印度", "PK": "巴基斯坦",
    "AU": "澳大利亚", "NZ": "新西兰",
    "ZA": "南非", "EG": "埃及", "NG": "尼日利亚", "KE": "肯尼亚",
}


def strip_leading_flags(name):
    return FLAG_RE.sub("", name or "").strip()


def flag_emoji(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


def geo_cache_key(host):
    return f"subman:geo:{host.lower()}"


def country_for_host(host):
    """返回主机所在国家的两位代码, 查不到返回 None。结果缓存于 KV。"""
    host = (host or "").strip().lower()
    if not host:
        return None
    cached = kv_get(geo_cache_key(host))
    if cached:
        return cached if len(cached) == 2 else None
    ip = None
    try:
        import ipaddress
        ipaddress.ip_address(host)
        ip = host
    except Exception:
        try:
            import socket
            ip = socket.gethostbyname(host)
        except Exception:
            return None
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,countryCode"
        req = urllib.request.Request(url, headers={"User-Agent": "subman-web/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("status") == "success" and d.get("countryCode"):
            cc = d["countryCode"].upper()
            kv_set(geo_cache_key(host), cc)
            return cc
    except Exception:
        pass
    return None


def _cc_map(hosts):
    hosts = {h for h in hosts if h}
    if not hosts:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for h, cc in zip(hosts, ex.map(country_for_host, hosts)):
            if cc:
                out[h] = cc
    return out


def enrich_flags(nodes):
    """给节点名加地区旗帜前缀。返回覆盖到的国家代码集合。"""
    cc_map = _cc_map({n["host"].lower() for n in nodes if n.get("host")})
    regions = set()
    for n in nodes:
        cc = cc_map.get((n.get("host") or "").lower())
        if not cc:
            continue
        regions.add(cc)
        base = strip_leading_flags(n.get("name") or n.get("host") or "")
        n["name"] = flag_emoji(cc) + base
        n["raw"] = rn.rename_node(n, n["name"])
    return regions


NAME_COUNTER_RE = re.compile(r"^(.*?)(\d+)$")


def _existing_counters(store):
    """扫描已有订阅节点名, 返回 {地区前缀: 最大编号}, 避免重名。"""
    valid = set(COUNTRY_ZH.values()) | set(COUNTRY_ZH.keys())
    counters = {}
    for sub in store["subs"].values():
        for ln in sub.get("nodes", []):
            n = ap.parse_full(ln)
            nm = strip_leading_flags((n.get("name") or "") if n else "")
            m = NAME_COUNTER_RE.match(nm)
            if m and m.group(1) in valid:
                counters[m.group(1)] = max(counters.get(m.group(1), 0),
                                           int(m.group(2)))
    return counters


def auto_rename_nodes(store, lines):
    """按节点地区自动重命名: 香港1、香港2…美国1…。编号接着已有订阅的最大值。"""
    parsed = [(ln, ap.parse_full(ln)) for ln in lines]
    cc_map = _cc_map({n["host"].lower() for _, n in parsed
                      if n and n.get("host")})
    counters = _existing_counters(store)
    out = []
    for ln, n in parsed:
        cc = cc_map.get((n.get("host") or "").lower()) if n else None
        if not n or not cc:
            out.append(ln)
            continue
        zh = COUNTRY_ZH.get(cc, cc)
        nxt = counters.get(zh, 0) + 1
        counters[zh] = nxt
        out.append(rn.rename_node(n, f"{zh}{nxt}"))
    return out


# ---------- IP 协议族筛选 ----------

def dns_cache_key(host):
    return f"subman:dns:{host.lower()}"


def ip_families(host):
    """返回 host 支持的协议族集合，如 {'v4','v6'}；解析失败返回 None。成功结果缓存。"""
    host = (host or "").strip().lower()
    if not host:
        return None
    cached = kv_get(dns_cache_key(host))
    if cached is not None:
        try:
            return set(json.loads(cached))
        except Exception:
            pass
    fams = set()
    try:
        import ipaddress
        v = ipaddress.ip_address(host).version
        fams.add("v4" if v == 4 else "v6")
    except ValueError:
        try:
            import socket
            for fam, *_ in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                if fam == socket.AF_INET:
                    fams.add("v4")
                elif fam == socket.AF_INET6:
                    fams.add("v6")
        except Exception:
            return None
    kv_set(dns_cache_key(host), json.dumps(sorted(fams)))
    return fams


def filter_by_ip_family(nodes, family):
    """只保留支持指定协议族的节点；解析不出结果的保留(fail-open)。"""
    hosts = {n["host"].lower() for n in nodes if n.get("host")}
    fam_map = {}
    if hosts:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            for h, f in zip(hosts, ex.map(ip_families, hosts)):
                fam_map[h] = f
    out = []
    for n in nodes:
        fams = fam_map.get((n.get("host") or "").lower())
        if fams is None or family in fams:
            out.append(n)
    return out


# ---------- 合成订阅 ----------

def build_bundle(store, allow_live_fetch=True):
    """返回 (base64文本, 节点数, 无法解析行数, [(订阅名, 行数, 备注)], [地区代码])."""
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
    ipf = b.get("ip_filter", "all")
    if ipf in ("v4", "v6") and nodes:
        nodes = filter_by_ip_family(nodes, ipf)
    regions = set()
    if b.get("flags", True) and nodes:
        regions = enrich_flags(nodes)
    out = base64.b64encode(
        ("\n".join(n["raw"] for n in nodes) + "\n").encode("utf-8")).decode()
    return out, len(nodes), unknown, info, sorted(regions)


def public_link(host, token):
    return f"https://{host}/s/{token}"
