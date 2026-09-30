"""GET/PUT /api/rules — routing rules for the dashboard.

GET /api/rules?business_id=<uuid> -> ARRAY of {t, s, c, icon, on} cards as
rendered by dashboard.html. Sourced from the business's routing_rules JSONB;
falls back to the routing-table-v1 defaults when none are stored.

PUT /api/rules?business_id=<uuid> with JSON array (or {"rules": [...]}) ->
persists the rules into routing_rules.

business_id is optional; without it the earliest-created business is used.
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


DEFAULT_RULES = [
    {"t": "Urgent care", "s": "urgent issues → page on-call staff",
     "c": "#ff6b5e", "icon": "ph-siren", "on": True},
    {"t": "Booking & reschedule", "s": "offer open slots → booking link",
     "c": "#8b5cf6", "icon": "ph-calendar-check", "on": True},
    {"t": "Pricing questions", "s": "answer from price sheet → auto-reply",
     "c": "#5b8cff", "icon": "ph-tag", "on": True},
    {"t": "Complaints", "s": "always → human review, flag owner",
     "c": "#f6bd8f", "icon": "ph-flag", "on": True},
    {"t": "Spam", "s": "drop + log at ≥0.95 confidence",
     "c": "#7fd4c1", "icon": "ph-trash", "on": True},
    {"t": "After hours", "s": "AI covers → morning digest at 7 AM",
     "c": "#94a3b8", "icon": "ph-moon-stars", "on": False},
]


def _resolve_business(business_id):
    if business_id:
        return shared.get_business(business_id)
    rows = shared.supa("GET", "receptionist_businesses",
                       params="?select=*&order=created_at.asc&limit=1")
    return rows[0] if rows else None


def _rules_for(business):
    rr = business.get("routing_rules") or {}
    if isinstance(rr, dict) and isinstance(rr.get("rules"), list) and rr["rules"]:
        return rr["rules"]
    return DEFAULT_RULES


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        business_id = (qs.get("business_id") or [None])[0]
        try:
            business = _resolve_business(business_id)
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        if not business:
            _send(self, 200, [])
            return
        _send(self, 200, _rules_for(business))

    def do_PUT(self):
        qs = parse_qs(urlparse(self.path).query)
        business_id = (qs.get("business_id") or [None])[0]
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length).decode() or "null")
        except Exception:
            _send(self, 400, {"error": "invalid JSON"})
            return
        rules = payload.get("rules") if isinstance(payload, dict) else payload
        if not isinstance(rules, list):
            _send(self, 400, {"error": "body must be a rules array or {\"rules\": [...]}"})
            return
        try:
            business = _resolve_business(business_id)
            if not business:
                _send(self, 404, {"error": "no business found"})
                return
            rows = shared.supa("PATCH", "receptionist_businesses",
                               {"routing_rules": {"rules": rules}},
                               params=f"?id=eq.{business['id']}")
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, (rows[0].get("routing_rules") or {}).get("rules", []))

    def log_message(self, *args):
        pass
