
from datetime import datetime, timezone
from uuid import uuid4

from app.database import database


def create_ticket(ticket_data):
    new_ticket = {
        "ticket_id": str(uuid4()),
        "customer_id": ticket_data["customer_id"],
        "title": ticket_data["title"],
        "category": ticket_data["category"],
        "description": ticket_data["description"],
        "source": ticket_data["source"],
        "status": "Open",
        "created_at": datetime.now(timezone.utc)
    }

    database.tickets.insert_one(new_ticket)

    return new_ticket


def get_ticket(ticket_id):
    return database.tickets.find_one(
        {"ticket_id": ticket_id},
        {"_id": 0}
    )


def update_ticket_status(ticket_id, status):
    allowed_statuses = [
        "Open",
        "In Progress",
        "Awaiting Reply",
        "Resolved"
    ]

    if status not in allowed_statuses:
        raise ValueError("Invalid ticket status")

    result = database.tickets.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": status}}
    )

    return result.matched_count > 0


def create_ticket_message(
    ticket_id,
    sender_id,
    sender_type,
    message,
    staff_name=None,
    attachments=None
):
    ticket = get_ticket(ticket_id)

    if not ticket:
        raise ValueError("Ticket not found")

    new_message = {
        "message_id": str(uuid4()),
        "ticket_id": ticket_id,
        "sender_id": str(sender_id),
        "sender_type": sender_type,
        "message": message,
        "created_at": datetime.now(timezone.utc),
        "is_read": False,
        "staff_name": staff_name,
        "attachments": attachments or []
    }

    database.messages.insert_one(new_message)

    return new_message


def save_discord_thread(ticket_id, guild_id, channel_id, thread_id):
    database.discord_threads.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "ticket_id": ticket_id,
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "thread_id": str(thread_id)
            }
        },
        upsert=True
    )
