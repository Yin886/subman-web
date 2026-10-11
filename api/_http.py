# api/_http.py — 极简 HTTP 响应小助手
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _send(h, code, body: bytes, ctype):
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def send_json(h, code, obj):
    _send(h, code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
          "application/json; charset=utf-8")


def send_text(h, code, text, ctype="text/plain; charset=utf-8"):
    _send(h, code, text.encode("utf-8"), ctype)


def need_auth(h):
    h.send_response(401)
    h.send_header("WWW-Authenticate", 'Basic realm="subman"')
    body = "需要登录".encode("utf-8")
    h.send_header("Content-Type", "text/plain; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def read_json(h):
    try:
        n = int(h.headers.get("Content-Length", 0) or 0)
    except Exception:
        n = 0
    if not n:
        return {}
    try:
        return json.loads(h.rfile.read(n).decode("utf-8"))
    except Exception:
        return {}


def headers_dict(h):
    return {k.lower(): v for k, v in h.headers.items()}
