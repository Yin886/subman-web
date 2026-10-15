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
        core.ensure_solo_token(store, name)
        out[name] = {"type": s["type"], "kind": s.get("kind", "uri"),
                     "count": s.get("count"),
                     "updated": s.get("fetched") or s.get("added"),
                     "solo_token": s.get("solo_token"),
                     "no_dedup": bool(s.get("no_dedup")),
                     "flags": s.get("flags", True),
                     "ip_filter": s.get("ip_filter", "all")}
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
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        store = core.load_store()
        if name:
            sub = store["subs"].get(name)
            if not sub:
                hh.send_json(self, 404, {"ok": False, "error": "没有这个订阅"})
                return
            hh.send_json(self, 200, {"ok": True,
                                    "sub": {"name": name, "type": sub["type"],
                                            "url": sub.get("url", ""),
                                            "nodes": "\n".join(
                                                sub.get("nodes", [])),
                                            "no_dedup": bool(sub.get("no_dedup")),
                                            "flags": sub.get("flags", True),
                                            "ip_filter": sub.get("ip_filter",
                                                                 "all"),
                                            "rename_prefix": sub.get(
                                                "rename_prefix", ""),
                                            "rename_start": sub.get(
                                                "rename_start", 1),
                                            "auto_rename": bool(sub.get(
                                                "auto_rename"))}})
            return
        summary = _summary(store)
        core.save_store(store)  # 补上老订阅缺失的 solo_token
        hh.send_json(self, 200, {"ok": True, "subs": summary})

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
        if action == "solo_token":
            body = hh.read_json(self)
            name = body.get("name", "")
            if name not in store["subs"]:
                hh.send_json(self, 404, {"ok": False, "error": "没有这个订阅"})
                return
            store["subs"][name]["solo_token"] = core.unique_token(store)
            core.save_store(store)
            hh.send_json(self, 200, {"ok": True, "subs": _summary(store)})
            return
        if action == "edit":
            body = hh.read_json(self)
            name = body.get("name", "")
            sub = store["subs"].get(name)
            if not sub:
                hh.send_json(self, 404, {"ok": False, "error": "没有这个订阅"})
                return
            if sub["type"] == "url":
                err = core.edit_url_sub(store, name, body.get("url", ""))
                if err:
                    hh.send_json(self, 400, {"ok": False, "error": err})
                    return
                err = core.set_sub_rename(store, name,
                                          body.get("rename_prefix", ""),
                                          body.get("rename_start", 1),
                                          body.get("auto_rename", False))
            else:
                err = core.edit_inline_sub(
                    store, name, body.get("nodes", ""),
                    body.get("rename_prefix", ""),
                    body.get("rename_start", 1),
                    body.get("auto_rename", False))
            if err:
                hh.send_json(self, 400, {"ok": False, "error": err})
                return
            if "no_dedup" in body or "flags" in body or "ip_filter" in body:
                err = core.set_sub_config(store, name, body.get("no_dedup"),
                                          body.get("flags", True),
                                          body.get("ip_filter", "all"))
                if err:
                    hh.send_json(self, 400, {"ok": False, "error": err})
                    return
            core.save_store(store)
            hh.send_json(self, 200, {"ok": True, "subs": _summary(store)})
            return
        body = hh.read_json(self)
        name, typ = body.get("name", ""), body.get("type", "")
        if typ == "url":
            err = core.add_url_sub(store, name, body.get("url", ""))
        elif typ == "inline":
            err = core.add_inline_sub(store, name, body.get("nodes", ""),
                                      body.get("rename_prefix", ""),
                                      body.get("rename_start", 1),
                                      body.get("auto_rename", False))
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
