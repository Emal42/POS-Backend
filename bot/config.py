"""Reads the settings from .env and stops with a clear message if any are missing."""
import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

REQUIRED = ["DISCORD_TOKEN", "TICKETS_CHANNEL_ID", "ALERTS_CHANNEL_ID", "SUPPORT_ROLE_ID", "TICKET_API_URL", "SHARED_SECRET"]
ID_SETTINGS = ["TICKETS_CHANNEL_ID", "ALERTS_CHANNEL_ID", "SUPPORT_ROLE_ID"]


@dataclass(frozen=True)
class Config:
    token: str
    tickets_channel_id: int
    alerts_channel_id: int
    support_role_id: int
    api_url: str
    secret: str
    port: int = 4000
    viewed_emoji: str = "👀"


def load_config():
    load_dotenv()
    missing = [key for key in REQUIRED if not os.environ.get(key)]
    if missing:
        sys.exit(f"Missing settings in .env: {', '.join(missing)}")

    not_numbers = [key for key in ID_SETTINGS if not os.environ[key].strip().isdigit()]
    if not_numbers:
        sys.exit(f"These .env settings must be Discord IDs (numbers only): {', '.join(not_numbers)}")

    return Config(
        token=os.environ["DISCORD_TOKEN"].strip(),
        tickets_channel_id=int(os.environ["TICKETS_CHANNEL_ID"]),
        alerts_channel_id=int(os.environ["ALERTS_CHANNEL_ID"]),
        support_role_id=int(os.environ["SUPPORT_ROLE_ID"]),
        api_url=os.environ["TICKET_API_URL"].rstrip("/"),
        secret=os.environ["SHARED_SECRET"],
        port=int(os.environ.get("BOT_PORT") or 4000),
    )
