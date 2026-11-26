
import os
import secrets

from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv()


def get_current_customer(
    x_dev_auth_key: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None)
):
    """
    Temporary customer authentication for local development.

    This will later be replaced with POSperity's
    real access-token authentication.
    """

    auth_mode = os.getenv("AUTH_MODE", "development").lower()
    dev_auth_key = os.getenv("DEV_AUTH_KEY")

    if auth_mode != "development":
        raise HTTPException(
            status_code=503,
            detail="Production authentication is not configured"
        )

    if not dev_auth_key:
        raise HTTPException(
            status_code=500,
            detail="DEV_AUTH_KEY is missing"
        )

    if not x_dev_auth_key or not secrets.compare_digest(
        x_dev_auth_key,
        dev_auth_key
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid development authentication key"
        )

    if not x_dev_user_id:
        raise HTTPException(
            status_code=401,
            detail="Customer ID is required"
        )

    return {
        "user_id": x_dev_user_id,
        "role": "customer"
    }
