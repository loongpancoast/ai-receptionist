# AI Receptionist — backend (Venture #3)

Jev-powered inbound message triage for local SMBs. One Jev call classifies +
routes every message. Pricing: $299/mo single tier on Whop (product on the
parallel Whop-MCP track).

## Endpoints (all under the production Vercel URL)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/triage` | Core: `{business_id, channel, from, text}` -> `{category, category_confidence, urgency, urgency_confidence, needs_human_now, route, action, jev_model, cost_usd}` |
| POST | `/api/webhook` | Twilio-style form POSTs (`Body/From/To`) or JSON -> triage + log, 200 TwiML/JSON |
| GET/POST/PUT/DELETE | `/api/businesses` | Business config + routing rules CRUD |
| GET | `/api/messages?business_id=` | Triage log, newest first (dashboard) |
| GET | `/api/queue?business_id=` | Dashboard queue feed (display-shaped cards; thin alias over messages) |
| GET/PUT | `/api/rules?business_id=` | Routing rules cards for the dashboard |
| POST | `/api/whop` | STUB — Whop webhook scaffolding, 501 until product exists |

## Calibration

Auto-actions fire ONLY at category confidence >= 0.95, enforced in
`shared.py::decide_route` — never left to the model. Complaints always go to
human review. Spam auto-drops only at >= 0.95.

## Env vars (Vercel project settings — never in the repo)

- `TYPESAFE_API_KEY` — TypeSafe/Jev API key (Secure Vault `custom.typesafe`).
- `SUPABASE_URL` — e.g. `https://<ref>.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — server-side only; RLS enabled with no public policies.
- `WHOP_WEBHOOK_SECRET` — later, when the Whop product exists.

## Deploy

Single WSGI entrypoint: `api.py` exposes `app`, declared in `pyproject.toml`
`[tool.vercel] entrypoint = "api:app"` (Vercel CLI 60+ requires this for new
Python projects — file-based `/api/*.py` functions no longer build). All
`/api/*` routes are handled inside that one function; shared logic
(Jev question pack v1, ≥0.95 auto-action gate, Supabase client) lives in
`shared.py`. Static files (`/`, `/dashboard.html`, `/config.js`, `/assets/*`)
are served by Vercel directly from the repo root — root copies are deploy
copies of `site/` (design track source of truth; only `config.js`'s
`__API_BASE__` value is managed by this track).

Git-based: push to GitHub, then `VERCEL_CREATE_NEW_DEPLOYMENT` with
`gitSource {"type":"github","repoId":"<string>","ref":"main","sha":"<sha>"}` and
`"target":"production"`. Redeploy after any env-var change. Diagnose SSO walls
with `VERCEL_GET_PROJECT2` (production must not be walled).

## Frontend

The design track owns `site/` (not deployed here). The dashboard reads the API
base from `window.__API_BASE__` in `site/config.js` — set it to this backend's
production URL; no backend rebuild needed.
