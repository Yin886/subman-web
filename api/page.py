# api/page.py — 管理后台页面(需登录)
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not core.check_auth(hh.headers_dict(self)):
            hh.need_auth(self)
            return
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
