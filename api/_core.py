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

CLASH_RE = re.compile(r"^\s*proxies\s*:", re.M)
# 有些订阅按 UA 返回不同格式(如 liangxin), 用浏览器 UA 拿通用 URI 格式
FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


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


def unique_token(store):
    seen = set()
    for b in store.get("bundles", {}).values():
        if b.get("token"):
            seen.add(b["token"])
    seen.update(s.get("solo_token") for s in store["subs"].values()
                if s.get("solo_token"))
    while True:
        t = new_token()
        if t not in seen:
            return t


def ensure_solo_token(store, name):
    """给订阅补独立发布 token(老数据迁移用), 返回 token。"""
    sub = store["subs"].get(name)
    if not sub:
        return None
    if not sub.get("solo_token"):
        sub["solo_token"] = unique_token(store)
    return sub["solo_token"]


def find_by_token(store, token):
    """公开链接 token 查找: 返回 (kind, name, cfg),
    kind 为 bundle/solo, cfg 为发布配置(单独链接用 solo_defaults)。"""
    if not token:
        return None
    for bname, b in store.get("bundles", {}).items():
        if b.get("token") == token:
            return ("bundle", bname, b)
    for name, s in store["subs"].items():
        if s.get("solo_token") == token:
            return ("solo", name, _solo_cfg(s))
    return None


def _prefix_sub_name(lines, sub_name):
    """节点名称前加上订阅名称, 如 'liangxin 香港1'，便于在组合里分辨来源。"""
    out = []
    for ln in lines:
        n = ap.parse_full(ln)
        if n:
            base = (n.get("name") or n.get("host") or "").strip()
            out.append(rn.rename_node(n, f"{sub_name} {base}".strip()))
        else:
            out.append(ln)
    return out


def default_bundle_cfg():
    return {"token": new_token(), "subs": [], "no_dedup": False,
            "flags": True, "ip_filter": "all", "region_filter": "",
            "sub_name_prefix": False, "region_rename": False,
            "updated": None}


def default_store():
    return {"subs": {}, "bundles": {}}


def _solo_cfg(sub):
    """单独订阅发布配置: 每个订阅独立。"""
    return {"subs": [], "no_dedup": bool(sub.get("no_dedup")),
            "flags": sub.get("flags", True),
            "ip_filter": sub.get("ip_filter", "all")}


def set_sub_config(store, name, no_dedup, flags, ip_filter):
    sub = store["subs"].get(name)
    if not sub:
        return "没有这个订阅"
    sub["no_dedup"] = bool(no_dedup)
    sub["flags"] = bool(flags)
    sub["ip_filter"] = ip_filter if ip_filter in ("all", "v4", "v6") else "all"
    return None


def _migrate(s):
    """老数据迁移:
    1) 单 bundle -> bundles['默认组合'], 发布配置 -> solo_defaults;
    2) solo_defaults -> 每个订阅独立的发布配置(之后单独链接各用各的)。"""
    changed = False
    if "bundle" in s and "bundles" not in s:
        old = s.pop("bundle") or {}
        s["bundles"] = {"默认组合": {
            "token": old.get("token") or new_token(),
            "subs": old.get("subs") or [],
            "no_dedup": bool(old.get("no_dedup")),
            "flags": old.get("flags", True),
            "ip_filter": old.get("ip_filter", "all"),
            "updated": old.get("updated")}}
        s["solo_defaults"] = {
            "no_dedup": bool(old.get("no_dedup")),
            "flags": old.get("flags", True),
            "ip_filter": old.get("ip_filter", "all")}
        changed = True
    sd = s.pop("solo_defaults", None)
    if sd is not None:
        for sub in s.get("subs", {}).values():
            sub.setdefault("no_dedup", bool(sd.get("no_dedup")))
            sub.setdefault("flags", sd.get("flags", True))
            sub.setdefault("ip_filter", sd.get("ip_filter", "all"))
        changed = True
    for _b in s.get("bundles", {}).values():
        if "region_filter" not in _b:
            _b["region_filter"] = ""
            changed = True
        if "sub_name_prefix" not in _b:
            _b["sub_name_prefix"] = False
            changed = True
        if "region_rename" not in _b:
            _b["region_rename"] = False
            changed = True
    for sub in s.get("subs", {}).values():
        for k, v in (("no_dedup", False), ("flags", True),
                     ("ip_filter", "all"), ("rename_prefix", ""),
                     ("rename_start", 1), ("auto_rename", False)):
            if k not in sub:
                sub[k] = v
                changed = True
    return changed


def load_store():
    raw = kv_get(STORE_KEY)
    if raw:
        try:
            s = json.loads(raw)
            migrated = _migrate(s)
            s.setdefault("subs", {})
            s.setdefault("bundles", {})
            if migrated:
                save_store(s)
            return s
        except Exception:
            pass
    return default_store()


def save_store(s):
    kv_set(STORE_KEY, json.dumps(s, ensure_ascii=False))


def cache_key(name):
    return f"subman:cache:{name}"


# ---------- 认证 ----------

ADMIN_KEY = "subman:admin"


def get_admin_creds():
    """KV 优先(KV 缺失时用环境变量初始化一次); KV 读失败回退环境变量。"""
    try:
        raw = kv_get(ADMIN_KEY)
        if raw:
            d = json.loads(raw)
            if d.get("user") and d.get("pass"):
                return d["user"], d["pass"]
        if ADMIN_USER and ADMIN_PASS:
            kv_set(ADMIN_KEY, json.dumps({"user": ADMIN_USER, "pass": ADMIN_PASS}))
            return ADMIN_USER, ADMIN_PASS
    except Exception:
        pass
    return ADMIN_USER, ADMIN_PASS


def set_admin_creds(user, pw):
    kv_set(ADMIN_KEY, json.dumps({"user": user, "pass": pw}))


def check_auth(headers):
    user, pw = get_admin_creds()
    if not user or not pw:
        return False
    auth = headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        u, p = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
    except Exception:
        return False
    return u == user and p == pw


# ---------- 订阅管理 ----------

def valid_name(name):
    name = (name or "").strip()
    if not name or len(name) > 64:
        return False
    return not any(c in name for c in "/\\\n\r")


SUB_NAME_ERR = "订阅名 1-64 个字符, 不能含 / 或换行"


def add_url_sub(store, name, url):
    name = (name or "").strip()
    if not valid_name(name):
        return SUB_NAME_ERR
    if name in store["subs"]:
        return f"已存在名为 {name} 的订阅"
    if not re.match(r"^https?://", url or "", re.I):
        return "URL 须以 http(s):// 开头"
    now = _now()
    store["subs"][name] = {"type": "url", "url": url, "kind": "uri",
                           "added": now, "fetched": None, "count": None,
                           "solo_token": unique_token(store),
                           "no_dedup": False, "flags": True, "ip_filter": "all"}
    return None


def _apply_rename(store, lines, rename_prefix="", rename_start=1,
                 auto_rename=False, exclude=None):
    """命名前缀优先, 否则按地区自动命名。返回重命名后的 lines。"""
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
        return renamed
    if auto_rename:
        return auto_rename_nodes(store, lines, exclude)
    return lines


def add_inline_sub(store, name, text, rename_prefix="", rename_start=1,
                   auto_rename=False):
    name = (name or "").strip()
    if not valid_name(name):
        return SUB_NAME_ERR
    if name in store["subs"]:
        return f"已存在名为 {name} 的订阅"
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and "://" in l]
    if (text or "").strip() and not lines:
        return "没有读到节点链接"
    lines = _apply_rename(store, lines, rename_prefix, rename_start,
                          auto_rename)
    now = _now()
    store["subs"][name] = {"type": "inline", "nodes": lines,
                           "added": now, "count": len(lines),
                           "solo_token": unique_token(store),
                           "no_dedup": False, "flags": True, "ip_filter": "all"}
    return None


def remove_sub(store, name):
    if name not in store["subs"]:
        return f"没有名为 {name} 的订阅"
    del store["subs"][name]
    kv_del(cache_key(name))
    for b in store.get("bundles", {}).values():
        if name in b.get("subs", []):
            b["subs"] = [n for n in b["subs"] if n != name]
    return None


def valid_bundle_name(name):
    name = (name or "").strip()
    if not name or len(name) > 64:
        return False
    return not any(c in name for c in "/\\\n\r")


def add_bundle(store, name):
    name = (name or "").strip()
    if not valid_bundle_name(name):
        return "组合名 1-64 个字符, 不能含 / 或换行"
    if name in store["bundles"]:
        return f"已存在名为 {name} 的组合"
    store["bundles"][name] = {
        "token": unique_token(store), "subs": [],
        "no_dedup": False, "flags": True, "ip_filter": "all",
        "region_filter": "", "sub_name_prefix": False,
        "region_rename": False, "updated": None}
    return None


def _region_match(cc, rf):
    """地区筛选匹配: rf 可为中文名(如 香港)或代码(如 HK)。"""
    rf = (rf or "").strip()
    if not rf or not cc:
        return False
    cc = cc.upper()
    return rf.upper() == cc or COUNTRY_ZH.get(cc) == rf


def filter_by_region(nodes, region_filter):
    """只保留指定地区的节点；地区未知时保留(fail-open)。"""
    rf = (region_filter or "").strip()
    if not rf:
        return nodes
    hosts = {n["host"].lower() for n in nodes if n.get("host")}
    cc_map = _cc_map(hosts) if hosts else {}
    return [n for n in nodes
            if not cc_map.get((n.get("host") or "").lower())
            or _region_match(cc_map.get((n.get("host") or "").lower()), rf)]


def update_bundle(store, name, subs, no_dedup, flags, ip_filter,
                   region_filter="", sub_name_prefix=False,
                   region_rename=False):
    b = store["bundles"].get(name)
    if not b:
        return "没有这个组合"
    b["subs"] = [n for n in (subs or []) if n in store["subs"]]
    b["no_dedup"] = bool(no_dedup)
    b["flags"] = bool(flags)
    b["ip_filter"] = ip_filter if ip_filter in ("all", "v4", "v6") else "all"
    b["region_filter"] = (region_filter or "").strip()
    b["sub_name_prefix"] = bool(sub_name_prefix)
    b["region_rename"] = bool(region_rename)
    b["updated"] = _now()
    return None


def remove_bundle(store, name):
    if name not in store["bundles"]:
        return "没有这个组合"
    del store["bundles"][name]
    return None


def regen_bundle_token(store, name):
    b = store["bundles"].get(name)
    if not b:
        return "没有这个组合"
    b["token"] = unique_token(store)
    b["updated"] = _now()
    return None


def edit_url_sub(store, name, url):
    sub = store["subs"].get(name)
    if not sub:
        return "没有这个订阅"
    if sub["type"] != "url":
        return f"{name} 不是远程订阅, 不能改链接"
    if not re.match(r"^https?://", url or "", re.I):
        return "URL 须以 http(s):// 开头"
    sub["url"] = url
    sub["kind"] = "uri"
    sub["count"] = None
    kv_del(cache_key(name))
    return None


def edit_inline_sub(store, name, text, rename_prefix="", rename_start=1,
                    auto_rename=False):
    """改内联订阅节点原文。text 为空时保留原节点(只改配置的场景)。
    填了命名前缀或勾选自动命名时, 对新节点重新命名。"""
    sub = store["subs"].get(name)
    if not sub:
        return "没有这个订阅"
    if sub["type"] != "inline":
        return f"{name} 不是粘贴订阅, 不能改节点"
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and "://" in l]
    if (text or "").strip() and not lines:
        return "没有读到节点链接"
    if lines:
        lines = _apply_rename(store, lines, rename_prefix, rename_start,
                              auto_rename, exclude=name)
        sub["nodes"] = lines
        sub["count"] = len(lines)
        sub["added"] = _now()
    return None


def fetch_sub(store, name):
    sub = store["subs"].get(name)
    if not sub:
        return "没有名为 %s 的订阅" % name
    if sub["type"] != "url":
        return f"{name} 是内联订阅, 无需拉取"
    try:
        raw, _desc = ap.fetch_source(sub["url"], ua=FETCH_UA)
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
    if cc == "TW":
        # 中国地区 iPhone 上 🇹🇼 会被系统显示成字母 "TW"，画不出旗帜；
        # 台湾节点改用纯文字命名（台湾1、台湾2…），保证正常显示。
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


def _existing_counters(store, exclude=None):
    """扫描已有订阅节点名, 返回 {地区前缀: 最大编号}, 避免重名。
    exclude: 排除某个订阅(编辑它时, 不把自己的旧节点算进去)。"""
    valid = set(COUNTRY_ZH.values()) | set(COUNTRY_ZH.keys())
    counters = {}
    for name, sub in store["subs"].items():
        if exclude and name == exclude:
            continue
        for ln in sub.get("nodes", []):
            n = ap.parse_full(ln)
            nm = strip_leading_flags((n.get("name") or "") if n else "")
            m = NAME_COUNTER_RE.match(nm)
            if m and m.group(1) in valid:
                counters[m.group(1)] = max(counters.get(m.group(1), 0),
                                           int(m.group(2)))
    return counters


def auto_rename_nodes(store, lines, exclude=None, counters=None):
    """按节点地区自动重命名: 香港1、香港2…美国1…。
    counters 传入时跨多次调用共享编号(组合内统一编号);
    不传时编号接着已有订阅的最大值。"""
    parsed = [(ln, ap.parse_full(ln)) for ln in lines]
    cc_map = _cc_map({n["host"].lower() for _, n in parsed
                      if n and n.get("host")})
    if counters is None:
        counters = _existing_counters(store, exclude)
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

def build_bundle(store, cfg, allow_live_fetch=True, only=None):
    """返回 (base64文本, 节点数, 无法解析行数, [(订阅名, 行数, 备注)], [地区代码]).
    cfg 为发布配置 {subs, no_dedup, flags, ip_filter};
    only 指定订阅名时只发布该订阅(单独链接)。"""
    if only:
        names = [only] if only in store["subs"] else []
    else:
        names = cfg.get("subs") or sorted(store["subs"].keys())
    chunks, info = [], []
    # 组合按地区统一命名时, 编号在组合内全局连续(香港1…香港N, 日本1…日本M)
    region_counters = {}
    for name in names:
        sub = store["subs"].get(name)
        if not sub:
            continue
        if sub["type"] == "inline":
            ls = sub["nodes"]
            if cfg.get("region_rename") and ls:
                ls = auto_rename_nodes(store, ls, counters=region_counters)
            if cfg.get("sub_name_prefix") and ls:
                ls = _prefix_sub_name(ls, name)
            chunks.append("\n".join(ls))
            info.append((name, len(ls), None))
            continue
        if sub.get("kind") == "clash":
            info.append((name, 0, "Clash 格式暂不支持, 已跳过"))
            continue
        raw = kv_get(cache_key(name))
        if raw is None and allow_live_fetch:
            try:
                raw, _ = ap.fetch_source(sub["url"], ua=FETCH_UA)
                kv_set(cache_key(name), raw if raw.endswith("\n") else raw + "\n")
            except Exception as e:
                info.append((name, 0, f"拉取失败: {e}"))
                continue
        if raw is None:
            info.append((name, 0, "无缓存, 请先点更新"))
            continue
        norm = ap.normalize(raw)
        ls = [l for l in norm.splitlines() if l.strip()]
        if cfg.get("region_rename") and ls:
            ls = auto_rename_nodes(store, ls, counters=region_counters)
        if cfg.get("sub_name_prefix") and ls:
            ls = _prefix_sub_name(ls, name)
        chunks.append("\n".join(ls))
        info.append((name, len(ls), None))
    text = "\n".join(chunks)
    nodes, unknown = ap.parse_subscription(text, dedup=not cfg.get("no_dedup"))
    rf = (cfg.get("region_filter") or "").strip()
    if rf and nodes:
        nodes = filter_by_region(nodes, rf)
    ipf = cfg.get("ip_filter", "all")
    if ipf in ("v4", "v6") and nodes:
        nodes = filter_by_ip_family(nodes, ipf)
    regions = set()
    if cfg.get("flags", True) and nodes:
        regions = enrich_flags(nodes)
    out = base64.b64encode(
        ("\n".join(n["raw"] for n in nodes) + "\n").encode("utf-8")).decode()
    return out, len(nodes), unknown, info, sorted(regions)


def public_link(host, token):
    return f"https://{host}/s/{token}"
