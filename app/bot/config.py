
import os
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass(frozen=True)
class Config:
    token: str
    tickets_channel_id: int
    alerts_channel_id: int
    support_role_id: int
    viewed_emoji: str = "👀"


def load_config():
    load_dotenv()

    required = [
        "DISCORD_BOT_TOKEN",
        "TICKETS_CHANNEL_ID",
        "ALERTS_CHANNEL_ID",
        "SUPPORT_ROLE_ID"
    ]

    missing = [
        key for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise ValueError(
            f"Missing settings in .env: {', '.join(missing)}"
        )

    return Config(
        token=os.getenv("DISCORD_BOT_TOKEN").strip(),
        tickets_channel_id=int(os.getenv("TICKETS_CHANNEL_ID")),
        alerts_channel_id=int(os.getenv("ALERTS_CHANNEL_ID")),
        support_role_id=int(os.getenv("SUPPORT_ROLE_ID"))
    )
