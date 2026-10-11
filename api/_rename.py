# api/_rename.py — 按新名字重写节点 URI 行内的显示名(vendored 自 nodeconv.rename_in_raw)
import base64
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _airportlib as ap  # noqa: E402


def rename_node(node, new_name):
    """按 new_name 重写 node['raw'] 中的显示名, 返回新行。"""
    raw, proto = node.get("raw") or "", node.get("proto") or ""
    if not raw or "://" not in raw:
        return raw
    scheme, rest = raw.split("://", 1)

    if proto == "vmess":
        body = rest.split("#")[0]
        frag = rest.split("#")[1] if "#" in rest else ""
        dec = ap.b64decode_text(body.split("?", 1)[0])
        if dec:
            try:
                j = json.loads(dec)
            except Exception:
                j = None
            if j is not None:
                j["ps"] = new_name
                nb = base64.b64encode(
                    json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode()
                ).decode()
                tail = f"#{urllib.parse.quote(new_name, safe='')}" if frag else ""
                return f"{scheme}://{nb}{tail}"
        # 明文格式(vmess://base64(auto:uuid@host:port)?...#name): 走通用 fragment
        body_q = rest.split("#")[0]
        return f"{scheme}://{body_q}#{urllib.parse.quote(new_name, safe='')}"

    if proto == "ssr":
        body = rest.split("#")[0]
        dec = ap.b64decode_text(body.split("/")[0].split("?")[0])
        if not dec:
            return raw
        enc_name = base64.b64encode(new_name.encode()).decode()
        if "remarks=" in dec:
            dec = re.sub(r"remarks=[^&]*",
                         "remarks=" + urllib.parse.quote(enc_name, safe=""), dec)
        else:
            if "/?" not in dec:
                dec += "/?"
            elif not dec.endswith("/?") and not dec.endswith("&"):
                dec += "&"
            dec += "remarks=" + urllib.parse.quote(enc_name, safe="")
        nb = base64.b64encode(dec.encode()).decode()
        return f"{scheme}://{nb}"

    # 通用: 替换 #fragment(ss/vless/trojan/anytls/hysteria2/socks 等名字都在 fragment 里)
    body = rest.split("#")[0]
    return f"{scheme}://{body}#{urllib.parse.quote(new_name, safe='')}"
