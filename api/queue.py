"""GET /api/queue — dashboard queue feed.

GET /api/queue?business_id=<uuid>&limit=<n> -> ARRAY of message cards shaped
for dashboard.html (fields: name, meta, text, cat, urg, sent, routed, note,
time, face, init, review). A thin alias over the receptionist_messages log.

business_id is optional; without it the earliest-created business is used
(the dashboard calls the bare path).
"""
import json
from datetime import datetime
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


CAT_LABEL = {"urgent_care": "Emergency", "booking": "Booking", "pricing": "Pricing",
             "complaint": "Complaint", "spam": "Spam", "other": "Other"}
ROUTED_LABEL = {"sms_oncall": "On-call staff paged", "booking_draft": "Booking draft",
                "pricing_reply": "Price sheet reply", "owner_flag": "Owner flagged",
                "drop": "Dropped (spam)", "review_queue": "Front desk review"}
PALETTE = ["#ff6b5e", "#8b5cf6", "#5b8cff", "#7fd4c1", "#f6bd8f", "#f472b6"]


def _urg_label(score):
    if score is None:
        return "Normal"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Normal"
    if s >= 2.5:
        return "Urgent"
    if s >= 1.5:
        return "High"
    if s >= 0.5:
        return "Normal"
    return "Low"


def _initials(name):
    parts = [p for p in name.replace("@", " ").replace(".", " ").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def _card(row):
    name = row.get("from_addr") or "Unknown contact"
    channel = row.get("channel") or "sms"
    cat = row.get("category") or "other"
    action = row.get("action") or "review"
    conf = row.get("jev_confidence")
    try:
        ts = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
        time_s = ts.strftime("%-I:%M %p")
    except Exception:
        time_s = ""
    color = PALETTE[abs(hash(name)) % len(PALETTE)]
    route = row.get("route") or "review_queue"
    return {
        "name": name,
        "meta": f"{channel} · {name}",
        "text": row.get("text") or "",
        "cat": CAT_LABEL.get(cat, "Other"),
        "urg": _urg_label(row.get("urgency")),
        "sent": "Neutral",  # sentiment not modeled yet; honest default
        "routed": ROUTED_LABEL.get(route, route),
        "note": f"auto · conf {conf}" if action == "auto" and conf is not None else "needs review",
        "time": time_s,
        "face": color,
        "init": _initials(name),
        "review": action != "auto",
    }


def _default_business():
    rows = shared.supa("GET", "receptionist_businesses",
                       params="?select=id&order=created_at.asc&limit=1")
    return rows[0]["id"] if rows else None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        business_id = (qs.get("business_id") or [None])[0]
        try:
            limit = min(max(int((qs.get("limit") or ["50"])[0]), 1), 200)
        except ValueError:
            limit = 50
        try:
            if not business_id:
                business_id = _default_business()
            if not business_id:
                _send(self, 200, [])
                return
            rows = shared.supa(
                "GET", "receptionist_messages",
                params=(f"?business_id=eq.{business_id}&select=*"
                        f"&order=created_at.desc&limit={limit}"))
        except RuntimeError as e:
            _send(self, 503, {"error": str(e)})
            return
        _send(self, 200, [_card(r) for r in rows])

    def log_message(self, *args):
        pass
