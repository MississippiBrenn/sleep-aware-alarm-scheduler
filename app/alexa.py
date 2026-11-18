"""Alexa has no public 'set an alarm' API, so we do the next best thing:
our scheduler decides the wake time, then at that moment pings a Voice Monkey
'Routine Trigger' device, which fires an Alexa Routine on your Echo (play an
alarm sound or music, set the volume, turn on lights, ...)."""
from __future__ import annotations

import asyncio

import httpx

from . import db
from .config import settings

TRIGGER_URL = "https://api-v3.voicemonkey.io/trigger"


def configured() -> bool:
    return bool(settings.voicemonkey_token and settings.voicemonkey_device)


async def trigger_routine() -> bool:
    if not configured():
        db.log("error", "Voice Monkey token or device ID is missing in .env")
        return False
    payload = {"token": settings.voicemonkey_token, "device": settings.voicemonkey_device}
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            try:
                r = await client.post(TRIGGER_URL, json=payload)
                if r.is_success:
                    db.log("info", "Alexa routine triggered")
                    return True
                db.log("warn", f"Voice Monkey replied {r.status_code}: {r.text[:120]}")
            except httpx.HTTPError as e:
                db.log("warn", f"Voice Monkey request failed: {e}")
            await asyncio.sleep(2**attempt)
    return False
