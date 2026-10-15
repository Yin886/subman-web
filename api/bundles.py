# api/bundles.py — 组合订阅管理(需登录)
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core as core  # noqa: E402
import _http as hh  # noqa: E402


def _bundle_info(store, host):
    out = {}
    for bname, b in store["bundles"].items():
        try:
            _o, count, unknown, _info, regions = core.build_bundle(
                store, b, allow_live_fetch=False)
        except Exception:
            count, unknown, regions = 0, 0, []
        out[bname] = {"subs": b["subs"], "no_dedup": b["no_dedup"],
                      "flags": b.get("flags", True),
                      "ip_filter": b.get("ip_filter", "all"),
                      "token": b["token"],
                      "link": core.public_link(host, b["token"]) if host else "",
                      "count": count, "unknown": unknown,
                      "regions": "".join(core.flag_emoji(c) for c in regions),
                      "updated": b["updated"]}
    return out


class handler(BaseHTTPRequestHandler):
    def _auth(self):
        if not core.check_auth(hh.headers_dict(self)):
            hh.need_auth(self)
            return False
        return True

    def _resp(self, store):
        host = hh.headers_dict(self).get("host", "")
        return {"ok": True, "bundles": _bundle_info(store, host),
                "all_subs": sorted(store["subs"].keys())}

    def do_GET(self):
        if not self._auth():
            return
        hh.send_json(self, 200, self._resp(core.load_store()))

    def do_POST(self):
        if not self._auth():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        action = (qs.get("action") or [""])[0]
        store = core.load_store()
        body = hh.read_json(self)
        name = (body.get("name") or "").strip()
        if action == "create":
            err = core.add_bundle(store, name)
        elif action == "update":
            err = core.update_bundle(store, name, body.get("subs") or [],
                                     body.get("no_dedup"),
                                     body.get("flags", True),
                                     body.get("ip_filter", "all"))
        elif action == "token":
            err = core.regen_bundle_token(store, name)
        elif action == "delete":
            err = core.remove_bundle(store, name)
        else:
            err = "未知操作"
        if err:
            hh.send_json(self, 400, {"ok": False, "error": err})
            return
        core.save_store(store)
        hh.send_json(self, 200, self._resp(store))

    def log_message(self, *a):
        pass
