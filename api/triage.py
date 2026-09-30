"""POST /api/triage — core classification endpoint.

Body: {business_id, channel, from, text, received_at?}
Runs one Jev call (question pack v1), applies the >=0.95 auto-action gate in
code, routes, logs to Supabase, and returns the triage decision.
"""
import json
from http.server import BaseHTTPRequestHandler

import shared


def _send(h, status, obj, ctype="application/json"):
    body = obj if isinstance(obj, (str, bytes)) else json.dumps(obj)
    if isinstance(body, str):
        body = body.encode()
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _send(self, 200, {
            "ok": True,
            "usage": "POST JSON {business_id, channel, from, text}",
            "contract": ["category", "category_confidence", "urgency",
                         "urgency_confidence", "needs_human_now", "route",
                         "action", "jev_model", "cost_usd"],
        })

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:
            _send(self, 400, {"error": "invalid JSON body"})
            return

        business_id = payload.get("business_id")
        text = (payload.get("text") or "").strip()
        channel = payload.get("channel") or "sms"
        from_addr = payload.get("from") or payload.get("from_addr")

        if not business_id or not text:
            _send(self, 400, {"error": "business_id and text are required"})
            return

        try:
            business = shared.get_business(business_id)
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        if not business:
            _send(self, 404, {"error": f"unknown business_id: {business_id}"})
            return

        try:
            result = shared.triage_message(business, channel, from_addr, text)
        except RuntimeError as e:
            # Missing TYPESAFE_API_KEY surfaces here as a clear 503, never a crash.
            _send(self, 503, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 - never leak tracebacks to callers
            _send(self, 502, {"error": f"triage failed: {type(e).__name__}"})
            return

        _send(self, 200, result)

    def log_message(self, *args):  # keep function logs clean
        pass
