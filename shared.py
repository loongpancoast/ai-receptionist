"""Shared triage logic for the AI Receptionist backend (project root, imported by api/*).
Vercel's Python runtime puts the project root on sys.path, so api/triage.py can
`import shared`. Files under api/ starting with "_" are ignored as endpoints,
which is why the shared module lives at the root instead.
The Jev question pack v1 below is the TESTED pack from the
build brief (verified 2026-09-30 against jev-1.13.0).
"""
import json
import os
import urllib.request

TYPESAFE_API = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

# ---------------------------------------------------------------- question pack v1
QUESTIONS = {
    "category": {
        "type": "choice",
        "instructions": "Classify the customer's inbound message by its primary intent.",
        "criteria": {
            "booking": "The customer wants to schedule, reschedule, confirm, or cancel an appointment or service visit.",
            "urgent_care": "The customer has an urgent problem needing same-day or emergency attention (pain, injury, damage, breakdown, lockout, leak, no heat/AC, health emergency).",
            "pricing": "The customer asks about prices, costs, quotes, estimates, or fees.",
            "complaint": "The customer expresses dissatisfaction, anger, or a problem with past service and wants it addressed or fixed.",
            "spam": "Unsolicited sales pitches, scams, robocall-style text, or clearly irrelevant promotional content.",
            "other": "None of the above categories fit this message.",
        },
    },
    "needs_human_now": {
        "type": "noul",
        "instructions": "This message needs a human to respond within the hour.",
    },
    "urgency": {
        "type": "score",
        "instructions": "How quickly does this message need a response?",
        "criteria": [
            "Routine: fine to answer in a few days",
            "Soon: should be answered today",
            "Hours: needs attention within a few hours",
            "Emergency: act now, immediate response needed",
        ],
    },
}

# Calibration rule (standing): auto-actions ONLY at confidence >= 0.95.
AUTO_CONFIDENCE_GATE = 0.95


def _env(name):
    v = os.environ.get(name)
    return v.strip() if v else None


def jev_decide(state):
    """One Jev call over HTTPS. Returns the raw TypeSafe response dict."""
    key = _env("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is not set on the server")
    body = json.dumps({"state": state, "questions": QUESTIONS, "model": JEV_MODEL}).encode()
    req = urllib.request.Request(
        TYPESAFE_API,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def supa(method, path, payload=None, params=""):
    """Minimal PostgREST client using the server-side service_role key."""
    base = _env("SUPABASE_URL")
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set on the server")
    url = base.rstrip("/") + "/rest/v1/" + path + params
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else []


def get_business(business_id):
    rows = supa("GET", "receptionist_businesses", params=f"?id=eq.{business_id}&select=*")
    return rows[0] if rows else None


def decide_route(category, cat_conf):
    """Routing table v1 + >=0.95 auto-action gate. Returns (route, action)."""
    if category == "spam":
        return ("drop", "auto" if cat_conf >= AUTO_CONFIDENCE_GATE else "review")
    if category == "complaint":
        return ("owner_flag", "review")  # human review always, flag owner
    if cat_conf < AUTO_CONFIDENCE_GATE:
        return ({"urgent_care": "review_queue", "booking": "booking_draft",
                 "pricing": "pricing_reply"}.get(category, "review_queue"), "review")
    return {
        "urgent_care": ("sms_oncall", "auto"),   # SMS/page on-call staff immediately
        "booking": ("booking_draft", "auto"),    # check calendar / draft booking reply
        "pricing": ("pricing_reply", "auto"),    # auto-reply with price sheet
    }.get(category, ("review_queue", "review"))


def triage_message(business, channel, from_addr, text):
    """Full pipeline: Jev decide -> gate -> route -> log. Returns the API response dict."""
    state = {
        "business": {"name": business.get("name"), "type": business.get("vertical")},
        "message": {"channel": channel, "from": from_addr, "text": text},
    }
    res = jev_decide(state)
    answers = res.get("answers", {})

    cat = answers.get("category", {})
    category = cat.get("choice", "other")
    cat_conf = float(cat.get("confidence", 0) or 0)

    nhn = answers.get("needs_human_now", {})
    needs_human = float(nhn.get("noul", 0.5) or 0.5)

    urg = answers.get("urgency", {})
    urgency = round(float(urg.get("score", 0) or 0), 2)
    urg_conf = round(float(urg.get("confidence", 0) or 0), 3)

    route, action = decide_route(category, cat_conf)

    usage = res.get("usage", {}) or {}
    in_tokens = usage.get("input_tokens", 0) or 0
    cost_usd = round(in_tokens * 0.042 / 1_000_000, 6)

    msg_rows = supa("POST", "receptionist_messages", {
        "business_id": business["id"],
        "channel": channel,
        "from_addr": from_addr,
        "text": text[:4000],
        "category": category,
        "urgency": urgency,
        "needs_human_now": needs_human,
        "route": route,
        "action": action,
        "jev_confidence": round(cat_conf, 3),
        "jev_model": res.get("model", JEV_MODEL),
    })
    message_id = msg_rows[0]["id"] if msg_rows else None

    return {
        "message_id": message_id,
        "business_id": business["id"],
        "category": category,
        "category_confidence": round(cat_conf, 3),
        "urgency": urgency,
        "urgency_confidence": urg_conf,
        "needs_human_now": round(needs_human, 3),
        "route": route,
        "action": action,  # "auto" | "review"
        "jev_model": res.get("model", JEV_MODEL),
        "cost_usd": cost_usd,
    }
