"""POST /api/webhook — inbound message webhook (Twilio / WhatsApp / generic).

Accepts Twilio-style application/x-www-form-urlencoded POSTs (Body, From, To)
AND generic JSON {business_id?, from, text, channel}. Runs triage, logs the
message, and returns 200: TwiML for Twilio callers, JSON otherwise.

business_id resolution: JSON field, ?business_id= query param, or the To
number matched against a business phone. No SMS is actually sent yet (no
Twilio numbers provisioned) — the intended action is logged instead.
"""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

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


TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _send(self, 200, {"ok": True,
                          "usage": "POST Twilio form fields or JSON {business_id, from, text, channel}"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode() if length else ""
        ctype = (self.headers.get("Content-Type") or "").lower()

        business_id = channel = from_addr = text = to_num = None
        is_twilio = "x-www-form-urlencoded" in ctype

        if is_twilio:
            form = parse_qs(raw)
            text = (form.get("Body") or [""])[0]
            from_addr = (form.get("From") or [None])[0]
            to_num = (form.get("To") or [None])[0]
            channel = "sms"
        else:
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                _send(self, 400, {"error": "invalid JSON body"})
                return
            business_id = payload.get("business_id")
            text = payload.get("text") or payload.get("Body")
            from_addr = payload.get("from") or payload.get("From")
            channel = payload.get("channel") or "sms"

        if not business_id:
            qs = parse_qs(urlparse(self.path).query)
            business_id = (qs.get("business_id") or [None])[0]

        text = (text or "").strip()
        if not text:
            _send(self, 400, {"error": "message text is required (Body / text)"})
            return

        try:
            if not business_id and to_num:
                rows = shared.supa("GET", "receptionist_businesses",
                                   params=f"?phone=eq.{to_num}&select=id&limit=1")
                if rows:
                    business_id = rows[0]["id"]
            business = shared.get_business(business_id) if business_id else None
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        if not business:
            _send(self, 400, {"error": "business_id required (?business_id=, JSON field, "
                                       "or register the To number on a business)"})
            return

        try:
            result = shared.triage_message(business, channel, from_addr, text)
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        except Exception:  # noqa: BLE001
            _send(self, 502, {"error": "triage failed"})
            return

        # Intended action is logged, not executed (no Twilio sender provisioned yet).
        intended = {
            "sms_oncall": f"WOULD_SEND_SMS to on-call staff for business {business['name']}",
            "booking_draft": "WOULD_DRAFT booking reply for review",
            "pricing_reply": "WOULD_SEND price-sheet reply" if result["action"] == "auto"
                             else "WOULD_DRAFT price-sheet reply for review",
            "owner_flag": f"WOULD_FLAG owner of {business['name']} (complaint)",
            "drop": "WOULD_DROP spam (logged only)",
            "review_queue": "QUEUED for human review",
        }.get(result["route"], "QUEUED for human review")
        print(f"[webhook] business={business['id']} route={result['route']} "
              f"action={result['action']}: {intended}")

        if is_twilio:
            _send(self, 200, TWIML, ctype="text/xml")
        else:
            _send(self, 200, {**result, "intended_action": intended})

    def log_message(self, *args):
        pass
