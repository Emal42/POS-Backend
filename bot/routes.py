"""Routes for calls from the REST server to the bot. Every reply is {"success": True|False, ...}.

Kept separate from discord.py and aiohttp so the routes can be tested without Discord.

handlers = {
    "create_ticket":  async fn(body)            -> dict
    "client_message": async fn(number, body)    -> dict
    "event":          async fn(number, body)    -> dict
    "alert":          async fn(body)            -> dict
    "is_ready":       fn()                      -> bool
}
"""
import json
import logging
import re

from .helpers import is_authorised

MESSAGE_ROUTE = re.compile(r"^/tickets/(\d+)/messages$")
EVENT_ROUTE = re.compile(r"^/tickets/(\d+)/events$")
MAX_BODY = 1_000_000

log = logging.getLogger("posperity.routes")


async def handle(method, path, headers, raw_body, secret, handlers):
    """Returns (status_code, json_dict)."""
    if method == "GET" and path == "/health":
        return 200, {"success": True, "discord": "connected" if handlers["is_ready"]() else "connecting"}

    if not is_authorised(headers, secret):
        return 401, {"success": False, "error": "Unauthorised"}

    if len(raw_body) > MAX_BODY:
        return 413, {"success": False, "error": "Body too large"}
    try:
        body = json.loads(raw_body) if raw_body else {}
    except ValueError:
        return 400, {"success": False, "error": "Invalid JSON"}
    if not isinstance(body, dict):
        return 400, {"success": False, "error": "Body must be a JSON object"}

    try:
        if method == "POST" and path == "/tickets":
            if not body.get("ticketNumber") or not body.get("title"):
                return 400, {"success": False, "error": "ticketNumber and title are required"}
            return 201, {"success": True, **await handlers["create_ticket"](body)}

        match = MESSAGE_ROUTE.match(path)
        if method == "POST" and match:
            return 200, {"success": True, **await handlers["client_message"](match.group(1), body)}

        match = EVENT_ROUTE.match(path)
        if method == "POST" and match:
            return 200, {"success": True, **await handlers["event"](match.group(1), body)}

        if method == "POST" and path == "/alerts":
            return 200, {"success": True, **await handlers["alert"](body)}

        return 404, {"success": False, "error": "Not found"}
    except Exception as err:  # the REST server treats 500 as "failed"
        log.exception("Route %s %s failed", method, path)
        return 500, {"success": False, "error": str(err)}
