"""GET /api/messages — message log listing for the dashboard.

GET /api/messages?business_id=<uuid>&limit=<n>  -> newest-first triage log
"""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import shared


def _send(h, status, obj):
    body = json.dumps(obj, default=str).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        business_id = (qs.get("business_id") or [None])[0]
        try:
            limit = min(max(int((qs.get("limit") or ["50"])[0]), 1), 200)
        except ValueError:
            limit = 50
        if not business_id:
            _send(self, 400, {"error": "business_id is required"})
            return
        try:
            rows = shared.supa(
                "GET", "receptionist_messages",
                params=(f"?business_id=eq.{business_id}&select=*"
                        f"&order=created_at.desc&limit={limit}"))
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, {"business_id": business_id, "count": len(rows), "messages": rows})

    def log_message(self, *args):
        pass
