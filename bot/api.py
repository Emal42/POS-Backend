"""Calls from the bot to the REST server (staff replies, status changes, "viewed")."""
import aiohttp


class TicketApi:
    def __init__(self, base_url, secret):
        self.base_url = base_url
        self.secret = secret
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self):
        if self.session:
            await self.session.close()

    async def _call(self, path, method, body=None):
        async with self.session.request(
            method,
            f"{self.base_url}{path}",
            json=body,
            headers={"x-shared-secret": self.secret},
        ) as res:
            if res.status >= 400:
                text = await res.text()
                raise RuntimeError(f"API {method} {path} failed ({res.status}) {text[:200]}")
            try:
                return await res.json(content_type=None)
            except ValueError:
                return {}

    # A staff reply to show in the client's chat
    async def send_staff_message(self, ticket_number, data):
        return await self._call(f"/bot/tickets/{ticket_number}/messages", "POST", data)

    # Status: "In progress", "Waiting for customer", "Resolved"
    async def set_status(self, ticket_number, status, staff_initials):
        return await self._call(f"/bot/tickets/{ticket_number}/status", "PATCH",
                                {"status": status, "staffInitials": staff_initials})

    # A staff member reacted 👀 to a client message -> the client sees the eye icon
    async def mark_viewed(self, ticket_number, message_id, staff_initials):
        return await self._call(f"/bot/tickets/{ticket_number}/messages/{message_id}/viewed", "PATCH",
                                {"staffInitials": staff_initials})
