"""GET/POST/PUT/DELETE /api/businesses — business config + routing rules CRUD.

GET    /api/businesses            -> list
GET    /api/businesses?id=<uuid>  -> one
POST   /api/businesses            -> create {name, vertical, phone, routing_rules, price_sheet}
PUT    /api/businesses?id=<uuid>  -> update (JSON body with fields to set)
DELETE /api/businesses?id=<uuid>  -> delete
"""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import shared


def _send(h, status, obj):
    body = json.dumps(obj).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _read_json(h):
    try:
        length = int(h.headers.get("Content-Length", 0) or 0)
    except ValueError:
        length = 0
    raw = h.rfile.read(length).decode() if length else "{}"
    try:
        return json.loads(raw or "{}")
    except Exception:
        return None


class handler(BaseHTTPRequestHandler):
    def _route(self):
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("id") or [None])[0]

    def do_GET(self):
        try:
            bid = self._route()
            params = "?select=*&order=created_at.desc"
            if bid:
                params = f"?id=eq.{bid}&select=*"
            rows = shared.supa("GET", "receptionist_businesses", params=params)
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, rows[0] if bid and rows else (rows if not bid else {}))
        if bid and not rows:
            pass  # 200 with {} keeps dashboard code simple; absence is visible

    def do_POST(self):
        payload = _read_json(self)
        if payload is None:
            _send(self, 400, {"error": "invalid JSON"})
            return
        if not payload.get("name"):
            _send(self, 400, {"error": "name is required"})
            return
        allowed = {"name", "vertical", "phone", "routing_rules", "price_sheet"}
        doc = {k: v for k, v in payload.items() if k in allowed}
        try:
            rows = shared.supa("POST", "receptionist_businesses", doc)
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 201, rows[0] if rows else {})

    def do_PUT(self):
        bid = self._route()
        payload = _read_json(self)
        if payload is None:
            _send(self, 400, {"error": "invalid JSON"})
            return
        if not bid:
            _send(self, 400, {"error": "?id= is required"})
            return
        allowed = {"name", "vertical", "phone", "routing_rules", "price_sheet"}
        doc = {k: v for k, v in payload.items() if k in allowed}
        if not doc:
            _send(self, 400, {"error": "nothing to update"})
            return
        try:
            rows = shared.supa("PATCH", "receptionist_businesses", doc, params=f"?id=eq.{bid}")
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, rows[0] if rows else {})

    def do_DELETE(self):
        bid = self._route()
        if not bid:
            _send(self, 400, {"error": "?id= is required"})
            return
        try:
            shared.supa("DELETE", "receptionist_businesses", params=f"?id=eq.{bid}")
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, {"deleted": bid})

    def log_message(self, *args):
        pass
