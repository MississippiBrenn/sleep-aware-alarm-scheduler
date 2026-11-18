from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import alexa, db, google_health, scheduler
from .config import settings
from .planner import Prefs, hm

TZ = ZoneInfo(settings.timezone)
STATIC = Path(__file__).parent / "static"
security = HTTPBasic()


def require_login(creds: HTTPBasicCredentials = Depends(security)) -> None:
    ok = bool(settings.admin_password) and secrets.compare_digest(
        creds.password.encode(), settings.admin_password.encode()
    )
    if not ok:
        raise HTTPException(401, "Wrong password", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.admin_password:
        raise RuntimeError("Set ADMIN_PASSWORD in .env before starting.")
    db.init()
    db.log("info", "Sleep alarm started")
    task = asyncio.create_task(scheduler.run_forever())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, dependencies=[Depends(require_login)])


def validate(p: Prefs) -> None:
    for value in (p.earliest, p.latest, p.typical_bedtime, p.monitor_from):
        hm(value)
    if hm(p.latest) <= hm(p.earliest):
        raise ValueError("The latest wake time must be after the earliest.")
    if hm(p.monitor_from).hour < 12:
        raise ValueError("Start watching in the evening (12:00 or later).")
    if not 4 <= p.target_hours <= 12:
        raise ValueError("Target sleep must be between 4 and 12 hours.")
    if not 60 <= p.cycle_minutes <= 130:
        raise ValueError("Sleep cycle length must be between 60 and 130 minutes.")
    if not 5 <= p.poll_minutes <= 30:
        raise ValueError("Check interval must be between 5 and 30 minutes.")
    if not p.days or any(d not in range(7) for d in p.days):
        raise ValueError("Pick at least one day.")


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {
        "now": datetime.now(TZ).isoformat(timespec="seconds"),
        "timezone": settings.timezone,
        "prefs": asdict(db.get_prefs()),
        "google": {"configured": google_health.configured(), "connected": google_health.connected()},
        "alexa": {"configured": alexa.configured()},
        "night": db.latest_night(),
        "events": db.recent_events(),
    }


@app.put("/api/prefs")
async def save_prefs(request: Request):
    body = await request.json()
    try:
        prefs = Prefs(**{**asdict(db.get_prefs()), **body})
        validate(prefs)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    db.set_prefs(prefs)
    scheduler.S.fetched_at = None  # re-plan right away
    return asdict(prefs)


@app.post("/api/test-alarm")
async def test_alarm():
    if not await alexa.trigger_routine():
        raise HTTPException(502, "Alexa didn't respond. Check the Voice Monkey token and device ID.")
    return {"ok": True}


@app.get("/api/debug/sleep")
async def debug_sleep():
    """Raw Google response plus how we parsed it. Use this after your first night."""
    try:
        raw = await google_health.fetch_sleep_raw(datetime.now(TZ), hours_back=36)
    except google_health.NotConnected:
        raise HTTPException(409, "Google Health isn't connected yet.")
    snap = google_health.parse_snapshot(raw)
    parsed = None
    if snap:
        parsed = {
            "onset": snap.onset.isoformat(),
            "data_through": snap.data_through.isoformat() if snap.data_through else None,
            "stages": [{"start": s.start.isoformat(), "end": s.end.isoformat(), "kind": s.kind} for s in snap.stages],
        }
    return {"parsed": parsed, "raw": raw}


@app.get("/auth/login")
def auth_login():
    if not google_health.configured():
        raise HTTPException(400, "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env first.")
    return RedirectResponse(google_health.build_auth_url())


@app.get("/auth/callback")
async def auth_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        raise HTTPException(400, f"Google sign-in failed: {error or 'no code returned'}")
    try:
        await google_health.finish_auth(code, state)
    except Exception as e:
        raise HTTPException(400, str(e))
    return RedirectResponse("/")
