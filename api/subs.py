# api/subs.py — 订阅增删改查(需登录)
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


def _summary(store):
    out = {}
    for name, s in store["subs"].items():
        out[name] = {"type": s["type"], "kind": s.get("kind", "uri"),
                     "count": s.get("count"),
                     "updated": s.get("fetched") or s.get("added")}
    return out


class handler(BaseHTTPRequestHandler):
    def _auth(self):
        if not core.check_auth(hh.headers_dict(self)):
            hh.need_auth(self)
            return False
        return True

    def do_GET(self):
        if not self._auth():
            return
        hh.send_json(self, 200, {"ok": True, "subs": _summary(core.load_store())})

    def do_POST(self):
        if not self._auth():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        action = (qs.get("action") or [""])[0]
        store = core.load_store()
        if action == "fetch":
            body = hh.read_json(self)
            names = [body["name"]] if body.get("name") else \
                [n for n, s in store["subs"].items() if s["type"] == "url"]
            results = []
            for n in names:
                err = core.fetch_sub(store, n)
                results.append({"name": n, "ok": err is None, "msg": err or "已更新"})
            core.save_store(store)
            hh.send_json(self, 200, {"ok": True, "results": results,
                                    "subs": _summary(store)})
            return
        body = hh.read_json(self)
        name, typ = body.get("name", ""), body.get("type", "")
        if typ == "url":
            err = core.add_url_sub(store, name, body.get("url", ""))
        elif typ == "inline":
            err = core.add_inline_sub(store, name, body.get("nodes", ""),
                                      body.get("rename_prefix", ""),
                                      body.get("rename_start", 1))
        else:
            err = "type 须为 url 或 inline"
        if err:
            hh.send_json(self, 400, {"ok": False, "error": err})
            return
        core.save_store(store)
        hh.send_json(self, 200, {"ok": True, "subs": _summary(store)})

    def do_DELETE(self):
        if not self._auth():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        store = core.load_store()
        err = core.remove_sub(store, name)
        if err:
            hh.send_json(self, 404, {"ok": False, "error": err})
            return
        core.save_store(store)
        hh.send_json(self, 200, {"ok": True, "subs": _summary(store)})

    def log_message(self, *a):
        pass
