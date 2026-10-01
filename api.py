"""AI Receptionist API — single WSGI entrypoint for Vercel's Python runtime.

Vercel (CLI 60+) requires new Python projects to declare one entrypoint via
[tool.vercel] in pyproject.toml instead of file-based /api functions. This
module exposes `app` (WSGI) and routes every /api/* path to the triage,
webhook, businesses, messages, queue, rules, and whop handlers. Static files
(/, /dashboard.html, /config.js, /assets/*) are served by Vercel directly.

All business logic (Jev question pack v1, >=0.95 auto-action gate, Supabase
PostgREST access) lives in shared.py.
"""
import json
from urllib.parse import parse_qs

import shared

TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


# ------------------------------------------------------------------ plumbing
def _read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length) if length else b""
    try:
        return json.loads(raw.decode() or "null")
    except Exception:
        return None  # signals invalid JSON


def _resp(start_response, status, obj, ctype="application/json"):
    body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, default=str)
    if isinstance(body, str):
        body = body.encode()
    code = {"200": "200 OK", "201": "201 Created", "400": "400 Bad Request",
            "404": "404 Not Found", "501": "501 Not Implemented",
            "502": "502 Bad Gateway", "503": "503 Service Unavailable"}[status]
    start_response(code, [("Content-Type", ctype), ("Content-Length", str(len(body)))])
    return [body]


def _q(environ):
    return parse_qs(environ.get("QUERY_STRING", ""))


def _one(qs, key, default=None):
    return (qs.get(key) or [default])[0]


def _default_business_id():
    rows = shared.supa("GET", "receptionist_businesses",
                       params="?select=id&order=created_at.asc&limit=1")
    return rows[0]["id"] if rows else None


# ------------------------------------------------------------------ handlers
def h_triage(environ, start_response):
    if environ["REQUEST_METHOD"] == "GET":
        return _resp(start_response, "200", {
            "ok": True, "usage": "POST JSON {business_id, channel, from, text}",
            "contract": ["category", "category_confidence", "urgency",
                         "urgency_confidence", "needs_human_now", "route",
                         "action", "jev_model", "cost_usd"]})
    if environ["REQUEST_METHOD"] != "POST":
        return _resp(start_response, "400", {"error": "use POST"})
    payload = _read_json(environ)
    if not isinstance(payload, dict):
        return _resp(start_response, "400", {"error": "invalid JSON body"})
    business_id = payload.get("business_id")
    text = (payload.get("text") or "").strip()
    channel = payload.get("channel") or "sms"
    from_addr = payload.get("from") or payload.get("from_addr")
    if not business_id or not text:
        return _resp(start_response, "400", {"error": "business_id and text are required"})
    try:
        business = shared.get_business(business_id)
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    if not business:
        return _resp(start_response, "404", {"error": f"unknown business_id: {business_id}"})
    try:
        result = shared.triage_message(business, channel, from_addr, text)
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    except Exception:  # noqa: BLE001
        return _resp(start_response, "502", {"error": "triage failed"})
    return _resp(start_response, "200", result)


def h_webhook(environ, start_response):
    method = environ["REQUEST_METHOD"]
    if method == "GET":
        return _resp(start_response, "200", {
            "ok": True, "usage": "POST Twilio form fields or JSON {business_id, from, text, channel}"})
    if method != "POST":
        return _resp(start_response, "400", {"error": "use POST"})
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length).decode() if length else ""
    ctype = (environ.get("CONTENT_TYPE") or "").lower()

    business_id = channel = from_addr = text = to_num = None
    is_twilio = "x-www-form-urlencoded" in ctype
    if is_twilio:
        form = parse_qs(raw)
        text = (form.get("Body") or [""])[0]
        from_addr = (form.get("From") or [None])[0]
        to_num = (form.get("To") or [None])[0]
        channel = "sms"
    else:
        payload = _read_json_raw(raw)
        if payload is None:
            return _resp(start_response, "400", {"error": "invalid JSON body"})
        business_id = payload.get("business_id")
        text = payload.get("text") or payload.get("Body")
        from_addr = payload.get("from") or payload.get("From")
        channel = payload.get("channel") or "sms"

    if not business_id:
        business_id = _one(_q(environ), "business_id")
    text = (text or "").strip()
    if not text:
        return _resp(start_response, "400", {"error": "message text is required (Body / text)"})
    try:
        if not business_id and to_num:
            rows = shared.supa("GET", "receptionist_businesses",
                               params=f"?phone=eq.{to_num}&select=id&limit=1")
            if rows:
                business_id = rows[0]["id"]
        business = shared.get_business(business_id) if business_id else None
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    if not business:
        return _resp(start_response, "400", {
            "error": "business_id required (?business_id=, JSON field, or register the To number)"})
    try:
        result = shared.triage_message(business, channel, from_addr, text)
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    except Exception:  # noqa: BLE001
        return _resp(start_response, "502", {"error": "triage failed"})

    intended = {
        "sms_oncall": f"WOULD_SEND_SMS to on-call staff for business {business['name']}",
        "booking_draft": "WOULD_DRAFT booking reply for review",
        "pricing_reply": ("WOULD_SEND price-sheet reply" if result["action"] == "auto"
                          else "WOULD_DRAFT price-sheet reply for review"),
        "owner_flag": f"WOULD_FLAG owner of {business['name']} (complaint)",
        "drop": "WOULD_DROP spam (logged only)",
        "review_queue": "QUEUED for human review",
    }.get(result["route"], "QUEUED for human review")
    print(f"[webhook] business={business['id']} route={result['route']} "
          f"action={result['action']}: {intended}")
    if is_twilio:
        return _resp(start_response, "200", TWIML, ctype="text/xml")
    return _resp(start_response, "200", {**result, "intended_action": intended})


def _read_json_raw(raw):
    try:
        return json.loads(raw or "null")
    except Exception:
        return None


def h_businesses(environ, start_response):
    method = environ["REQUEST_METHOD"]
    bid = _one(_q(environ), "id")
    try:
        if method == "GET":
            params = "?select=*&order=created_at.desc"
            if bid:
                params = f"?id=eq.{bid}&select=*"
            rows = shared.supa("GET", "receptionist_businesses", params=params)
            return _resp(start_response, "200", rows[0] if bid and rows else (rows if not bid else {}))
        payload = _read_json(environ)
        if not isinstance(payload, dict):
            return _resp(start_response, "400", {"error": "invalid JSON"})
        allowed = {"name", "vertical", "phone", "routing_rules", "price_sheet"}
        if method == "POST":
            if not payload.get("name"):
                return _resp(start_response, "400", {"error": "name is required"})
            rows = shared.supa("POST", "receptionist_businesses",
                               {k: v for k, v in payload.items() if k in allowed})
            return _resp(start_response, "201", rows[0] if rows else {})
        if method in ("PUT", "PATCH"):
            if not bid:
                return _resp(start_response, "400", {"error": "?id= is required"})
            doc = {k: v for k, v in payload.items() if k in allowed}
            if not doc:
                return _resp(start_response, "400", {"error": "nothing to update"})
            rows = shared.supa("PATCH", "receptionist_businesses", doc, params=f"?id=eq.{bid}")
            return _resp(start_response, "200", rows[0] if rows else {})
        if method == "DELETE":
            if not bid:
                return _resp(start_response, "400", {"error": "?id= is required"})
            shared.supa("DELETE", "receptionist_businesses", params=f"?id=eq.{bid}")
            return _resp(start_response, "200", {"deleted": bid})
        return _resp(start_response, "400", {"error": "unsupported method"})
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})


def h_messages(environ, start_response):
    qs = _q(environ)
    business_id = _one(qs, "business_id")
    try:
        limit = min(max(int(_one(qs, "limit", "50")), 1), 200)
    except ValueError:
        limit = 50
    if not business_id:
        return _resp(start_response, "400", {"error": "business_id is required"})
    try:
        rows = shared.supa("GET", "receptionist_messages",
                           params=(f"?business_id=eq.{business_id}&select=*"
                                   f"&order=created_at.desc&limit={limit}"))
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    return _resp(start_response, "200",
                 {"business_id": business_id, "count": len(rows), "messages": rows})


CAT_LABEL = {"urgent_care": "Emergency", "booking": "Booking", "pricing": "Pricing",
             "complaint": "Complaint", "spam": "Spam", "other": "Other"}
ROUTED_LABEL = {"sms_oncall": "On-call staff paged", "booking_draft": "Booking draft",
                "pricing_reply": "Price sheet reply", "owner_flag": "Owner flagged",
                "drop": "Dropped (spam)", "review_queue": "Front desk review"}
PALETTE = ["#ff6b5e", "#8b5cf6", "#5b8cff", "#7fd4c1", "#f6bd8f", "#f472b6"]


def _urg_label(score):
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


def _card(row):
    from datetime import datetime
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
    parts = [p for p in name.replace("@", " ").replace(".", " ").split() if p]
    return {
        "name": name,
        "meta": f"{channel} · {name}",
        "text": row.get("text") or "",
        "cat": CAT_LABEL.get(cat, "Other"),
        "urg": _urg_label(row.get("urgency")),
        "sent": "Neutral",
        "routed": ROUTED_LABEL.get(route, route),
        "note": (f"auto · conf {conf}" if action == "auto" and conf is not None
                 else "needs review"),
        "time": time_s,
        "face": color,
        "init": ("".join(p[0] for p in parts[:2]) or "?").upper(),
        "review": action != "auto",
    }


def h_queue(environ, start_response):
    qs = _q(environ)
    business_id = _one(qs, "business_id")
    try:
        limit = min(max(int(_one(qs, "limit", "50")), 1), 200)
    except ValueError:
        limit = 50
    try:
        if not business_id:
            business_id = _default_business_id()
        if not business_id:
            return _resp(start_response, "200", [])
        rows = shared.supa("GET", "receptionist_messages",
                           params=(f"?business_id=eq.{business_id}&select=*"
                                   f"&order=created_at.desc&limit={limit}"))
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})
    return _resp(start_response, "200", [_card(r) for r in rows])


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


def _rules_for(business):
    rr = business.get("routing_rules") or {}
    if isinstance(rr, dict) and isinstance(rr.get("rules"), list) and rr["rules"]:
        return rr["rules"]
    return DEFAULT_RULES


def h_rules(environ, start_response):
    method = environ["REQUEST_METHOD"]
    business_id = _one(_q(environ), "business_id")
    try:
        if method == "GET":
            business = (shared.get_business(business_id) if business_id
                        else _default_business_row())
            if not business:
                return _resp(start_response, "200", [])
            return _resp(start_response, "200", _rules_for(business))
        if method in ("PUT", "PATCH", "POST"):
            payload = _read_json(environ)
            rules = payload.get("rules") if isinstance(payload, dict) else payload
            if not isinstance(rules, list):
                return _resp(start_response, "400",
                             {"error": 'body must be a rules array or {"rules": [...]}'})
            business = (shared.get_business(business_id) if business_id
                        else _default_business_row())
            if not business:
                return _resp(start_response, "404", {"error": "no business found"})
            rows = shared.supa("PATCH", "receptionist_businesses",
                               {"routing_rules": {"rules": rules}},
                               params=f"?id=eq.{business['id']}")
            return _resp(start_response, "200",
                         (rows[0].get("routing_rules") or {}).get("rules", []))
        return _resp(start_response, "400", {"error": "unsupported method"})
    except RuntimeError as e:
        return _resp(start_response, "503", {"error": str(e)})


def _default_business_row():
    rows = shared.supa("GET", "receptionist_businesses",
                       params="?select=*&order=created_at.asc&limit=1")
    return rows[0] if rows else None


def h_whop(environ, start_response):
    import hashlib
    import hmac
    import os
    if environ["REQUEST_METHOD"] == "GET":
        return _resp(start_response, "200", {
            "ok": True, "status": "stub",
            "note": "Whop product pending; wire the product + WHOP_WEBHOOK_SECRET, then implement."})
    if environ["REQUEST_METHOD"] != "POST":
        return _resp(start_response, "400", {"error": "use POST"})
    secret = os.environ.get("WHOP_WEBHOOK_SECRET")
    if not secret:
        return _resp(start_response, "501", {
            "status": "not_configured",
            "todo": ("Set WHOP_WEBHOOK_SECRET (+ WHOP_PRODUCT_ID) env vars, then implement "
                     "signature verification and event handling. Whop product creation "
                     "is on the parallel track.")})
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length) if length else b""
    sig = None
    for k, v in environ.items():
        if k.startswith("HTTP_") and k.replace("HTTP_", "").replace("_", "-").lower() in (
                "x-whop-signature", "whop-signature"):
            sig = v
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    verified = bool(sig) and hmac.compare_digest(sig, expected)
    return _resp(start_response, "200", {
        "status": "stub", "signature_verified": verified,
        "todo": "Wire event handling once the Whop product exists."})


ROUTES = {
    "/api/triage": h_triage,
    "/api/webhook": h_webhook,
    "/api/businesses": h_businesses,
    "/api/messages": h_messages,
    "/api/queue": h_queue,
    "/api/rules": h_rules,
    "/api/whop": h_whop,
}

# ------------------------------------------------------------------ static
# With a declared [tool.vercel] entrypoint, Vercel routes every request to
# this app — static files are NOT served separately. So the app serves the
# deploy-tree static files itself (whitelisted; no path traversal).
import os

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8", "no-cache"),
    "/index.html": ("index.html", "text/html; charset=utf-8", "no-cache"),
    "/dashboard.html": ("dashboard.html", "text/html; charset=utf-8", "no-cache"),
    "/config.js": ("config.js", "application/javascript; charset=utf-8", "no-cache"),
    "/llms.txt": ("llms.txt", "text/plain; charset=utf-8", "public, max-age=3600"),
    "/robots.txt": ("robots.txt", "text/plain; charset=utf-8", "public, max-age=3600"),
}
STATIC_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".svg": "image/svg+xml", ".webp": "image/webp", ".ico": "image/x-icon"}


def _serve_static(path, start_response):
    if path in STATIC_FILES:
        fname, mime, cache = STATIC_FILES[path]
    elif path.startswith("/assets/"):
        fname = path.lstrip("/")
        ext = os.path.splitext(fname)[1].lower()
        mime = STATIC_MIME.get(ext)
        cache = "public, max-age=31536000, immutable"
        if not mime or ".." in fname:
            return None
    else:
        return None
    fpath = os.path.join(os.getcwd(), fname)
    if not os.path.isfile(fpath):
        return None
    with open(fpath, "rb") as f:
        body = f.read()
    start_response("200 OK", [("Content-Type", mime),
                              ("Content-Length", str(len(body))),
                              ("Cache-Control", cache)])
    return [body]


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "").rstrip("/") or "/"
    handler = ROUTES.get(path)
    if handler:
        try:
            return handler(environ, start_response)
        except Exception as e:  # noqa: BLE001 - never leak tracebacks
            import urllib.error
            if isinstance(e, urllib.error.HTTPError):
                return _resp(start_response, "400",
                             {"error": f"datastore rejected the request (HTTP {e.code})"})
            return _resp(start_response, "502", {"error": f"handler failed: {type(e).__name__}"})
    static = _serve_static(path, start_response)
    if static is not None:
        return static
    return _resp(start_response, "404", {"error": f"unknown path: {path}"})
