# api/bundle.py — 发布配置(需登录)
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _auth(self):
        if not core.check_auth(hh.headers_dict(self)):
            hh.need_auth(self)
            return False
        return True

    def _bundle_info(self, store):
        b = store["bundle"]
        host = hh.headers_dict(self).get("host", "")
        try:
            _out, count, unknown, info, regions = core.build_bundle(
                store, allow_live_fetch=False)
        except Exception as e:
            count, unknown, info, regions = 0, 0, [["-", 0, f"预览失败: {e}"]], []
        return {"bundle": {"subs": b["subs"], "no_dedup": b["no_dedup"],
                           "flags": b.get("flags", True),
                           "updated": b["updated"], "token": b["token"]},
                "link": core.public_link(host, b["token"]) if host else "",
                "count": count, "unknown": unknown,
                "regions": "".join(core.flag_emoji(c) for c in regions),
                "info": [{"name": n, "lines": c, "note": t} for n, c, t in info],
                "all_subs": sorted(store["subs"].keys())}

    def do_GET(self):
        if not self._auth():
            return
        hh.send_json(self, 200, {"ok": True, **self._bundle_info(core.load_store())})

    def do_POST(self):
        if not self._auth():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        action = (qs.get("action") or [""])[0]
        store = core.load_store()
        if action == "token":
            store["bundle"]["token"] = core.new_token()
            store["bundle"]["updated"] = core._now()
            core.save_store(store)
            hh.send_json(self, 200, {"ok": True, **self._bundle_info(store)})
            return
        body = hh.read_json(self)
        subs = body.get("subs") or []
        unknown = [n for n in subs if n not in store["subs"]]
        if unknown:
            hh.send_json(self, 400, {"ok": False,
                                     "error": "不存在的订阅: " + ", ".join(unknown)})
            return
        store["bundle"]["subs"] = subs
        store["bundle"]["no_dedup"] = bool(body.get("no_dedup"))
        store["bundle"]["flags"] = bool(body.get("flags", True))
        store["bundle"]["updated"] = core._now()
        core.save_store(store)
        hh.send_json(self, 200, {"ok": True, **self._bundle_info(store)})

    def log_message(self, *a):
        pass
