#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
airportlib.py — 机场订阅解析与检测的公共库

被 check_airport.py(检测) 与 substore.py(订阅转换) 共用。

parse_full(line) 返回的节点结构:
{
    "raw":   原始链接行(转换输出时原样使用,保证不丢参数),
    "name":  节点名,
    "proto": 协议(ss/ssr/vmess/vless/trojan/tuic/hysteria2/socks5/http...),
    "host":  服务器地址,
    "port":  端口,
    "params": 协议相关参数 dict(各协议字段见 parse_full 内注释),
}
"""

import base64
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "clash-verge/v2.0"

# 解析到这些网段的 IP 视为可疑(网关/DNS劫持/内网),机场节点应为公网 IP
SUSPICIOUS_NETS = [ipaddress.ip_network(c) for c in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10",
    "198.18.0.0/15", "198.19.0.0/16",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
)]


# ---------- 基础工具 ----------

def b64decode_text(s):
    s = re.sub(r"\s+", "", s or "")
    if not s:
        return None
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        raw = base64.b64decode(s)
    except Exception:
        return None
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def fetch_source(source, ua=None):
    """返回 (原始文本, 脱敏后的来源描述)。source 可为 URL 或本地文件路径。"""
    if re.match(r"^https?://", source, re.I):
        req = urllib.request.Request(source, headers={"User-Agent": ua or UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
        u = urllib.parse.urlsplit(source)
        desc = f"{u.scheme}://{u.netloc}{u.path}"  # 去掉 query 里的 token
        return raw, desc
    path = os.path.expanduser(source)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(), f"本地文件 {source}"


def normalize(raw):
    """订阅整体可能是 base64 包一层,解开它"""
    text = raw.lstrip("﻿").strip()
    head = text[:300]
    if "://" not in head and "proxies" not in head:
        dec = b64decode_text(text)
        if dec and ("://" in dec[:500] or "proxies" in dec[:500]):
            return dec
    return text


def split_hostport(hp):
    hp = hp.strip()
    if hp.startswith("["):
        m = re.match(r"\[(.+)\](?::(\d+))?", hp)
        if m:
            return m.group(1), int(m.group(2)) if m.group(2) else 0
        return hp, 0
    if hp.count(":") > 1:
        return hp, 0
    if ":" in hp:
        h, p = hp.rsplit(":", 1)
        return h, int(p) if p.isdigit() else 0
    return hp, 0


def _query_dict(qs):
    out = {}
    for k, v in urllib.parse.parse_qsl(qs, keep_blank_values=True):
        out[k] = v
    return out


# ---------- 节点解析 ----------

def parse_full(line):
    """解析单行节点链接,返回完整节点 dict,失败返回 None。"""
    raw = line.strip().strip("\"").strip("'")
    if not raw or raw.startswith("#") or "://" not in raw:
        return None
    scheme, rest = raw.split("://", 1)
    scheme = scheme.lower()
    try:
        # ---- vmess: vmess://base64(json) ----
        if scheme == "vmess":
            dec = b64decode_text(rest.split("#")[0].split("?", 1)[0])
            if not dec:
                return None
            try:
                j = json.loads(dec)
            except Exception:
                j = None
            if j is not None:
                return {"raw": raw, "name": j.get("ps", ""), "proto": "vmess",
                        "host": j.get("add", ""), "port": int(j.get("port") or 0),
                        "params": j}
            # Shadowrocket 明文格式: vmess://base64(method:uuid@host:port)?remarks=..&obfs=websocket&tls=1#name
            m = re.match(r"\s*([^:]*):([^@]+)@([^:@]+):(\d+)\s*$", dec)
            if not m:
                return None
            method, uid, host, port = m.groups()
            body_q = rest.split("#", 1)[0]
            q = _query_dict(body_q.split("?", 1)[1] if "?" in body_q else "")
            frag = rest.split("#", 1)[1] if "#" in rest else ""
            name = urllib.parse.unquote(frag) if frag else urllib.parse.unquote(q.get("remarks", ""))
            obfs = q.get("obfs", "")
            return {"raw": raw, "name": name, "proto": "vmess",
                    "host": host, "port": int(port),
                    "params": {"id": uid, "scy": method or "auto",
                               "net": "ws" if obfs == "websocket" else "tcp",
                               "tls": "tls" if q.get("tls") == "1" else "",
                               "query": q}}

        # ---- ssr: ssr://base64(host:port:proto:method:obfs:pwd_b64/?params) ----
        if scheme == "ssr":
            body = rest.split("#")[0]
            dec = b64decode_text(body.split("/")[0].split("?")[0])
            if not dec:
                return None
            main = dec.split("/?")[0]
            parts = main.split(":")
            if len(parts) < 6:
                return None
            pwd = b64decode_text(parts[5]) or ""
            q = _query_dict(dec.split("/?", 1)[1] if "/?" in dec else "")
            remarks = b64decode_text(urllib.parse.unquote(q.get("remarks", ""))) or ""
            return {"raw": raw, "name": remarks, "proto": "ssr",
                    "host": parts[0], "port": int(parts[1]) if parts[1].isdigit() else 0,
                    "params": {"protocol": parts[2], "method": parts[3],
                               "obfs": parts[4], "password": pwd, "query": q}}

        # ---- ss ----
        if scheme == "ss":
            body, frag = (rest.split("#", 1) + [""])[:2]
            name = urllib.parse.unquote(frag)
            # 分离 plugin 查询参数
            plugin = ""
            if "/?" in body:
                body, plug_q = body.split("/?", 1)
                pq = _query_dict(plug_q)
                plugin = urllib.parse.unquote(pq.get("plugin", ""))
            elif "?" in body and "@" in body.split("?")[0]:
                body, plug_q = body.split("?", 1)
                pq = _query_dict(plug_q)
                plugin = urllib.parse.unquote(pq.get("plugin", ""))
            hostport, userinfo = None, None
            if "@" in body:
                userinfo_enc, hostport = body.rsplit("@", 1)
                userinfo = b64decode_text(userinfo_enc)
                if userinfo is None:  # 明文 method:password@host:port
                    userinfo = urllib.parse.unquote(userinfo_enc)
            else:
                dec = b64decode_text(body) or ""
                if "@" in dec:
                    userinfo, hostport = dec.rsplit("@", 1)
            if not hostport or not userinfo or ":" not in userinfo:
                return None
            method, password = userinfo.split(":", 1)
            host, port = split_hostport(hostport)
            return {"raw": raw, "name": name, "proto": "ss",
                    "host": host, "port": port,
                    "params": {"method": method, "password": password,
                               "plugin": plugin}}

        # ---- 通用 userinfo@host:port 形式 ----
        u = urllib.parse.urlsplit(raw)
        host = u.hostname or ""
        port = u.port or 0
        name = urllib.parse.unquote(u.fragment or "")
        if not host or not port:
            return None
        if scheme in ("vless",):
            # vless://uuid@host:port?params#name
            user = urllib.parse.unquote(u.username or "")
            return {"raw": raw, "name": name, "proto": "vless",
                    "host": host, "port": port,
                    "params": {"uuid": user, "query": _query_dict(u.query)}}
        if scheme in ("trojan",):
            pwd = urllib.parse.unquote(u.username or "")
            return {"raw": raw, "name": name, "proto": "trojan",
                    "host": host, "port": port,
                    "params": {"password": pwd, "query": _query_dict(u.query)}}
        if scheme in ("tuic",):
            user = urllib.parse.unquote(u.username or "")
            pwd = urllib.parse.unquote(u.password or "")
            return {"raw": raw, "name": name, "proto": "tuic",
                    "host": host, "port": port,
                    "params": {"uuid": user, "password": pwd,
                               "query": _query_dict(u.query)}}
        if scheme in ("hysteria2", "hy2"):
            pwd = urllib.parse.unquote(u.username or "")
            return {"raw": raw, "name": name, "proto": "hysteria2",
                    "host": host, "port": port,
                    "params": {"password": pwd, "query": _query_dict(u.query)}}
        if scheme in ("socks", "socks5"):
            return {"raw": raw, "name": name, "proto": "socks5",
                    "host": host, "port": port,
                    "params": {"username": urllib.parse.unquote(u.username or ""),
                               "password": urllib.parse.unquote(u.password or "")}}
        if scheme in ("http", "https"):
            return {"raw": raw, "name": name, "proto": "http",
                    "host": host, "port": port,
                    "params": {"username": urllib.parse.unquote(u.username or ""),
                               "password": urllib.parse.unquote(u.password or ""),
                               "tls": scheme == "https"}}
        return {"raw": raw, "name": name, "proto": scheme,
                "host": host, "port": port, "params": {}}
    except Exception:
        return None


def clash_to_node(p):
    """Clash proxy dict -> 内部节点格式(与 parse_full 的输出结构一致),
    这样下游的检测/转换逻辑无需区分节点来源。"""
    t = str(p.get("type", "")).lower()
    name = str(p.get("name", ""))
    host = str(p.get("server", ""))
    try:
        port = int(p.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not port:
        return None
    base = {"raw": "", "name": name, "host": host, "port": port}

    if t == "ss":
        return {**base, "proto": "ss", "params": {
            "method": p.get("cipher", ""), "password": str(p.get("password", "")),
            "plugin": str(p.get("plugin", ""))}}

    if t == "ssr":
        return {**base, "proto": "ssr", "params": {
            "protocol": p.get("protocol", ""), "method": p.get("cipher", ""),
            "obfs": p.get("obfs", ""), "password": str(p.get("password", "")),
            "query": {}}}

    if t == "vmess":
        j = {"ps": name, "add": host, "port": str(port),
             "id": str(p.get("uuid", "")), "aid": str(p.get("alterId", 0)),
             "scy": p.get("cipher", "auto") or "auto",
             "net": p.get("network", "tcp") or "tcp",
             "type": "", "host": "", "path": "", "tls": "", "sni": ""}
        ws_opts = p.get("ws-opts") or {}
        headers = ws_opts.get("headers") or {}
        j["path"] = ws_opts.get("path", "")
        j["host"] = headers.get("Host", "")
        h2_opts = p.get("h2-opts") or {}
        if j["net"] == "h2":
            j["host"] = ",".join(h2_opts.get("host") or [])
            j["path"] = h2_opts.get("path", "")
        grpc_opts = p.get("grpc-opts") or {}
        if j["net"] == "grpc":
            j["path"] = grpc_opts.get("grpc-service-name", "")
        http_opts = p.get("http-opts") or {}
        if j["net"] == "tcp" and http_opts:
            j["type"] = "http"
            j["path"] = ",".join(http_opts.get("path") or [])
            j["host"] = ",".join((http_opts.get("headers") or {}).get("Host") or [])
        if p.get("tls"):
            j["tls"] = "tls"
            j["sni"] = p.get("servername") or j["host"] or host
        return {**base, "proto": "vmess", "params": j}

    if t == "vless":
        q = {"type": p.get("network", "tcp") or "tcp"}
        if p.get("tls"):
            q["security"] = "tls"
        if p.get("servername"):
            q["sni"] = p["servername"]
        if p.get("flow"):
            q["flow"] = p["flow"]
        if p.get("client-fingerprint"):
            q["fp"] = p["client-fingerprint"]
        ro = p.get("reality-opts") or {}
        if ro:
            q["security"] = "reality"
            q["pbk"] = ro.get("public-key", "")
            if ro.get("short-id"):
                q["sid"] = ro["short-id"]
        ws_opts = p.get("ws-opts") or {}
        if ws_opts:
            q["path"] = ws_opts.get("path", "")
            headers = ws_opts.get("headers") or {}
            if headers.get("Host"):
                q["host"] = headers["Host"]
        grpc_opts = p.get("grpc-opts") or {}
        if grpc_opts:
            q["serviceName"] = grpc_opts.get("grpc-service-name", "")
        return {**base, "proto": "vless", "params": {
            "uuid": str(p.get("uuid", "")), "query": q}}

    if t == "trojan":
        return {**base, "proto": "trojan", "params": {
            "password": str(p.get("password", "")),
            "query": {"sni": p.get("sni") or host}}}

    if t == "hysteria2":
        params = {"password": str(p.get("password", "") or p.get("auth-str", "")),
                  "query": {}}
        if p.get("sni"):
            params["query"]["sni"] = p["sni"]
        if p.get("skip-cert-verify"):
            params["query"]["insecure"] = "1"
            params["skip_tls_verify"] = True
        ports = p.get("ports") or p.get("mport") or ""
        if ports:
            params["port_hopping"] = str(ports)
        if p.get("fingerprint"):
            params["cert_fingerprint"] = str(p["fingerprint"])
        return {**base, "proto": "hysteria2", "params": params}

    if t == "socks5":
        return {**base, "proto": "socks5", "params": {
            "username": str(p.get("username", "")),
            "password": str(p.get("password", ""))}}

    if t == "http":
        return {**base, "proto": "http", "params": {
            "username": str(p.get("username", "")),
            "password": str(p.get("password", "")),
            "tls": bool(p.get("tls"))}}

    return {**base, "proto": t, "params": {"query": {}}}


def parse_clash(text):
    """解析 clash YAML,返回 (节点列表, 无法解析数)。"""
    nodes = []
    try:
        import yaml
        data = yaml.safe_load(text)
        proxies = data.get("proxies", []) if isinstance(data, dict) else []
        for p in proxies:
            if isinstance(p, dict):
                n = clash_to_node(p)
                if n:
                    nodes.append(n)
        return nodes, 0
    except ImportError:
        pass
    except Exception:
        return [], 0
    # 无 pyyaml 时的简易解析兜底(仅提取基本字段)
    cur = {}
    def flush():
        if cur.get("server") and cur.get("port"):
            n = clash_to_node({"name": cur.get("name", ""),
                               "type": cur.get("type", ""),
                               "server": cur["server"], "port": cur["port"]})
            if n:
                nodes.append(n)
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("- "):
            flush()
            cur = {}
            m = re.match(r"-\s*name\s*:\s*(.+)", s)
            if m:
                cur["name"] = m.group(1).strip().strip("\"'")
            continue
        m = re.match(r"(name|server|port|type)\s*:\s*(.+)", s)
        if m:
            cur[m.group(1)] = m.group(2).strip().strip("\"'")
    flush()
    return nodes, 0


def parse_subscription(text, dedup=True):
    """解析订阅文本,返回 (节点列表, 无法解析的行数)。
    dedup=True 时按 (proto/host/port) 去重(默认,保持原有行为)。"""
    text = normalize(text)
    if re.search(r"^\s*proxies\s*:", text, re.M):
        return parse_clash(text)
    nodes, unknown = [], 0
    seen = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        n = parse_full(ln)
        if n and n["host"] and n["port"]:
            key = (n["proto"], n["host"].lower(), n["port"])
            if dedup and key in seen:
                continue
            seen.add(key)
            nodes.append(n)
        else:
            unknown += 1
    return nodes, unknown


# ---------- 连通性检测 ----------

def dns_poison_probe():
    """探测本机 DNS 是否劫持: 解析一个必定不存在的域名。
    返回劫持到的 IP 集合,空集合表示 DNS 正常。"""
    probe = f"nxprobe-{uuid.uuid4().hex[:8]}.invalid"
    try:
        infos = socket.getaddrinfo(probe, 80, type=socket.SOCK_STREAM)
        return {i[4][0] for i in infos}
    except Exception:
        return set()


def tcp_intercept_probe(timeout=5):
    """探测本机网关是否代答所有 TCP 连接: 尝试连接公网 IP 的必死端口。
    若连通,说明出境 TCP 被拦截代答,存活检测结果作废,返回 True。"""
    for host, port in (("8.8.8.8", 9999), ("1.1.1.1", 9999)):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except Exception:
            continue
    return False


def ip_suspicious(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(addr in net for net in SUSPICIOUS_NETS)


def tcp_check(node, timeout, poison_ips):
    host, port = node["host"], node["port"]
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return {**node, "status": "dead", "latency_ms": None, "reason": "DNS解析失败"}
    ips = {i[4][0] for i in infos}
    if (ips & poison_ips) or any(ip_suspicious(ip) for ip in ips):
        return {**node, "status": "suspect", "latency_ms": None,
                "reason": "解析到网关/劫持IP,结果不可信"}
    err = ""
    for fam, typ, proto, _canon, sa in infos:
        try:
            s = socket.socket(fam, typ, proto)
            s.settimeout(timeout)
            s.connect(sa)
            s.close()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {**node, "status": "alive", "latency_ms": ms, "reason": ""}
        except Exception as e:
            err = type(e).__name__
    return {**node, "status": "dead", "latency_ms": None, "reason": err or "连接失败"}


def check_all(nodes, timeout, workers, poison_ips):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(tcp_check, n, timeout, poison_ips): n for n in nodes}
        for f in as_completed(futs):
            results.append(f.result())
    return results
