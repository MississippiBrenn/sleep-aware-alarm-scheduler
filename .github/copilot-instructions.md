# Copilot instructions

## Big picture
- This is a FastAPI + SQLite overnight alarm scheduler. It polls Google Health sleep data, chooses a wake time, and triggers an Alexa routine through Voice Monkey.
- The expected flow is: `app/static/index.html` → FastAPI routes in `app/main.py` → `app/scheduler.py` → `app/planner.py` → `app/google_health.py` or `app/alexa.py`.
- `app/planner.py` is intentionally pure (no network, DB, or I/O). Keep wake-window math and live-stage nudges there; use `tests/test_planner.py` for it.
- SQLite is the only durable state: `app/db.py` stores prefs/tokens/night records/events in `kv`, `nights`, and `events`. OAuth tokens and `DB_PATH` data are sensitive; keep `data/` and `.env` untracked.
- The dashboard is a no-build vanilla HTML/JS/CSS app in `app/static/index.html`; API payloads are consumed directly rather than through a frontend framework.

## Critical workflows
- Setup: `cp .env.example .env`, then `pip install -r requirements.txt`. `ADMIN_PASSWORD` is required at startup; Google/Voice Monkey credentials are optional for server startup but required for integrations.
- Run locally: `uvicorn app.main:app --host 127.0.0.1 --port 8000`, then open `http://localhost:8000` (HTTP Basic username is ignored).
- Test: `.venv/bin/python -m pytest -q` is the local workspace command; Docker uses Python 3.12 and `python -m pytest` is sufficient in an environment with requirements installed.
- Docker: `docker build -t sleep-alarm .`, then run with `--env-file .env -v "$PWD/data:/srv/data" -p 127.0.0.1:8000:8000`. Port 8000 plus a localhost OAuth redirect are intentional; do not expose plain HTTP publicly.
- Debug sleep parsing with `GET /api/debug/sleep` (also in the dashboard). This is the primary place to inspect Google responses and `parse_snapshot` output.

## Runtime and integration conventions
- `app/config.py` loads `.env` once and exposes one frozen `Settings` object; environment names are defined in `.env.example`. Inline `#` comments in env values are stripped.
- Use `ZoneInfo(settings.timezone)` consistently. Datetimes in persisted/API data may carry timezone offsets, while UTC timestamps are common in `app/db.py` event and fired records.
- `app/main.py` is the API/admin boundary: every route is protected by the HTTP Basic dependency, except the static file route inherits the app dependency. Validate pref ranges in `validate()` before merging/DB writes.
- Preserve pref compatibility in `app/db.py`: `get_prefs()` filters stored JSON through `dataclasses.fields(Prefs)`, and prefs are written with `asdict(Prefs)`.
- Use `Prefs(days=[weekday numbers])` with Monday=0. `target_wake_date()` distinguishes evening monitoring (it targets the next morning) from active pre-wake window behavior.

## Safety and external-service behavior
- The latest wake time is a hard deadline. Planner, Google, or token failures must never move a wake past `latest`; scheduler code in `app/scheduler.py` also fails safe after the retry window.
- Polling and state live in `S` (`app/scheduler.py`). Reset `S` pieces when a prefs change or a new wake date begins; keep the last good sleep snapshot on transient fetch failures.
- Google Health failures fall back to an estimated bedtime plan via `plan_wake(..., snap=None)`. Stage nudges are trusted only for data no older than 20 minutes.
- Voice Monkey is a single HTTP POST in `app/alexa.py`; retain its 3-attempt/2,4-second retry pattern and non-2xx/HTTPError handling. `scheduler.tick()` separately retries failed wake calls every 30 seconds until 10 minutes past latest.
- Google parsing is defensive: session/stage field names vary, `_kind()` normalizes common values, and the longest sleep session is selected. Update `parse_snapshot()` carefully and use `/api/debug/sleep` as the contract check.
