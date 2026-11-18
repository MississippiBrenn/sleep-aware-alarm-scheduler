import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    # Strip inline "# comments" so .env.example can be copied as-is.
    return os.getenv(name, default).split("#")[0].strip()


@dataclass(frozen=True)
class Settings:
    timezone: str = _env("TIMEZONE", "America/Los_Angeles")
    admin_password: str = _env("ADMIN_PASSWORD")
    db_path: str = _env("DB_PATH", "data/sleepalarm.db")
    google_client_id: str = _env("GOOGLE_CLIENT_ID")
    google_client_secret: str = _env("GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = _env("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    voicemonkey_token: str = _env("VOICEMONKEY_TOKEN")
    voicemonkey_device: str = _env("VOICEMONKEY_DEVICE")


settings = Settings()
