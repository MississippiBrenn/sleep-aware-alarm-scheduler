"""Google Health API (v4): OAuth + reading the current night's sleep session.

This replaces the retired Google Fit REST API. Sleep needs only one scope:
googlehealth.sleep.readonly. The sleep list endpoint can only be filtered by
session END time, so a session that is still in progress may not show up until
your watch syncs it. When nothing is found the planner falls back to your usual
bedtime, and the deadline alarm always fires.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from . import db
from .config import settings
from .planner import Snapshot, Stage

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
SLEEP_URL = "https://health.googleapis.com/v4/users/me/dataTypes/sleep/dataPoints"


class NotConnected(Exception):
    pass


def configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def connected() -> bool:
    return bool(db.get_json("google_tokens"))


def build_auth_url() -> str:
    state = secrets.token_urlsafe(24)
    db.set_json("oauth_state", state)
    return AUTH_URL + "?" + urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


async def _token_request(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={**data, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret},
        )
    r.raise_for_status()
    return r.json()


async def finish_auth(code: str, state: str) -> None:
    if not secrets.compare_digest(state, db.get_json("oauth_state", "") or "x"):
        raise ValueError("OAuth state mismatch; start the connection again.")
    tok = await _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.google_redirect_uri}
    )
    db.set_json(
        "google_tokens",
        {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + tok.get("expires_in", 3600),
        },
    )
    db.log("info", "Google Health connected")


async def _access_token() -> str:
    tok = db.get_json("google_tokens")
    if not tok:
        raise NotConnected()
    if tok["expires_at"] - time.time() > 60:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise NotConnected()
    fresh = await _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    tok.update(access_token=fresh["access_token"], expires_at=time.time() + fresh.get("expires_in", 3600))
    db.set_json("google_tokens", tok)
    return tok["access_token"]


async def fetch_sleep_raw(now: datetime, hours_back: int = 20) -> dict:
    token = await _access_token()
    since = (now - timedelta(hours=hours_back)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            SLEEP_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"filter": f'sleep.interval.end_time >= "{since}"', "pageSize": "25"},
        )
    r.raise_for_status()
    return r.json()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_KINDS = {"awake": "awake", "wake": "awake", "restless": "awake", "light": "light", "deep": "deep",
          "rem": "rem", "asleep": "asleep", "sleep": "asleep"}


def _kind(raw: str) -> str:
    raw = (raw or "").lower().replace("sleep_stage_", "").replace("stage_", "")
    return _KINDS.get(raw, "asleep")


def parse_snapshot(payload: dict) -> Snapshot | None:
    """Pick the main sleep session (longest) and turn it into a Snapshot."""
    best = None
    for point in payload.get("dataPoints", []):
        s = point.get("sleep") or {}
        iv = s.get("interval") or {}
        if not (iv.get("startTime") and iv.get("endTime")):
            continue
        start, end = _ts(iv["startTime"]), _ts(iv["endTime"])
        if best is None or (end - start) > (best[1] - best[0]):
            best = (start, end, s)
    if best is None:
        return None
    start, end, s = best
    stages = []
    for st in s.get("stages", []):
        a, b = st.get("startTime"), st.get("endTime")
        if a and b:
            stages.append(Stage(_ts(a), _ts(b), _kind(st.get("type") or st.get("stage") or "")))
    stages.sort(key=lambda x: x.start)
    through = stages[-1].end if stages else end
    return Snapshot(onset=start, stages=stages, data_through=through)


async def fetch_snapshot(now: datetime) -> Snapshot | None:
    return parse_snapshot(await fetch_sleep_raw(now))
