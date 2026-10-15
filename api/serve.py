# api/serve.py — 公开订阅链接(无需登录, token 即权限)
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (qs.get("token") or [""])[0]
        store = core.load_store()
        found = core.find_by_token(store, token)
        if not found:
            hh.send_text(self, 404, "not found")
            return
        kind, name = found
        try:
            out, _count, _un, _info, _regions = core.build_bundle(
                store, allow_live_fetch=True,
                only=name if kind == "solo" else None)
        except Exception as e:
            hh.send_text(self, 500, f"生成订阅失败: {e}")
            return
        hh.send_text(self, 200, out + "\n")

    def log_message(self, *a):
        pass
