# api/admin.py — 后台账号管理(需登录)
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not core.check_auth(hh.headers_dict(self)):
            hh.need_auth(self)
            return
        body = hh.read_json(self)
        if body.get("action") != "passwd":
            hh.send_json(self, 400, {"ok": False, "error": "未知操作"})
            return
        user = (body.get("user") or "").strip()
        pw = body.get("pass") or ""
        if not user or not pw:
            hh.send_json(self, 400, {"ok": False, "error": "用户名和密码不能为空"})
            return
        if len(user) > 64 or len(pw) > 128 or ":" in user:
            hh.send_json(self, 400, {"ok": False, "error": "用户名/密码格式不对"})
            return
        core.set_admin_creds(user, pw)
        hh.send_json(self, 200, {"ok": True})

    def log_message(self, *a):
        pass
