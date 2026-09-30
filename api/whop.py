"""POST /api/whop — Whop webhook receiver (STUB).

The Whop product ($299/mo single tier) does not exist yet — creation is on the
parallel Whop-MCP track. This stub holds the verification scaffolding so the
wiring is one step once the product exists:

  1. Create the product in Whop, add a webhook endpoint pointing here.
  2. Set WHOP_WEBHOOK_SECRET (and WHOP_PRODUCT_ID) as Vercel env vars.
  3. Replace the TODO below with real event handling (provision business,
     map whop user -> business_id, handle cancellations).

No plan IDs are invented here.
"""
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler


def _send(h, status, obj):
    body = json.dumps(obj).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _send(self, 200, {"ok": True, "status": "stub",
                          "note": "Whop product pending; see module docstring for wiring steps."})

    def do_POST(self):
        secret = os.environ.get("WHOP_WEBHOOK_SECRET")
        if not secret:
            _send(self, 501, {
                "status": "not_configured",
                "todo": ("Set WHOP_WEBHOOK_SECRET (+ WHOP_PRODUCT_ID) env vars, then "
                         "implement signature verification and event handling. "
                         "Whop product creation is on the parallel track."),
            })
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        # Signature-verification scaffolding (exact header/payload scheme to be
        # confirmed against Whop's docs when the product is created).
        sig = self.headers.get("X-Whop-Signature") or self.headers.get("Whop-Signature")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        verified = bool(sig) and hmac.compare_digest(sig, expected)
        _send(self, 200, {"status": "stub", "signature_verified": verified,
                          "todo": "Wire event handling once the Whop product exists."})

    def log_message(self, *args):
        pass
