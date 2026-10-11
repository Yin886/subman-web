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
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_page.html")
            with open(p, "rb") as f:
                body = f.read()
        except Exception as e:
            d = os.path.dirname(os.path.abspath(__file__))
            try:
                ls = ", ".join(sorted(os.listdir(d)))
            except Exception as e2:
                ls = f"listdir failed: {e2}"
            hh.send_text(self, 500,
                         f"DEBUG page load failed: {e}\nfile={__file__}\ndir={d}\nls=[{ls}]")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass
