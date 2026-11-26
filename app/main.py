from fastapi import FastAPI
from app.database import check_database, database
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import HTTPException
import os
import secrets
from dotenv import load_dotenv
from fastapi import Depends, Header
from fastapi import UploadFile, File
from pathlib import Path
import shutil
from fastapi.responses import FileResponse
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from pydantic import Field
from typing import Literal

limiter = Limiter(key_func=get_remote_address)

app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler
)

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


def verify_discord_bot(x_bot_token: str = Header(default="")):

    if not DISCORD_BOT_TOKEN or not secrets.compare_digest(
        x_bot_token, DISCORD_BOT_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Discord bot token"
        )

class Ticket(BaseModel):
    customer_id: str = Field(min_length=1)
    title: str = Field(min_length=3, max_length=100)
    category: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=2000)
    source: Literal["main_pos", "sales_website"]
class Message(BaseModel):
    sender_id: str
    sender_type: str
    message: str
class DiscordThread(BaseModel):
    guild_id: str
    channel_id: str
    thread_id: str
class StaffReply(BaseModel):
    staff_id: str
    message: str
class Feedback(BaseModel):
    customer_id: str
    comment: str = ""

MAX_FILE_SIZE = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".pdf"]

UPLOAD_FOLDER = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)

@app.get("/")
def home():
    return {"message": "POSperity Support API is running"}

@app.get("/test-database")
def test_database():
    if check_database():
        return {"message": "MongoDB connection successful!"}

    return {"message": "MongoDB connection failed"}

@app.post("/tickets")
@limiter.limit("5/minute")
def create_ticket(request: Request, ticket: Ticket):

    new_ticket = {
        "ticket_id": str(uuid4()),
        "customer_id": ticket.customer_id,
        "title": ticket.title,
        "category": ticket.category,
        "description": ticket.description,
        "source": ticket.source,
        "status": "Open",
        "created_at": datetime.now(timezone.utc)
    }

    database.tickets.insert_one(new_ticket)

    return {
        "message": "Ticket created successfully",
        "ticket_id": new_ticket["ticket_id"]
    }



@app.get("/tickets")
def get_tickets(
    status: str = None,
    customer_id: str = None,
    source: str = None,
    search: str = None,
    date_from: str = None,
    date_to: str = None
):
    query = {}

    if status:
        query["status"] = status

    if customer_id:
        query["customer_id"] = customer_id

    if source:
        query["source"] = source

    if search:
        query["$or"] = [
            {"ticket_id": {"$regex": search, "$options": "i"}},
            {"title": {"$regex": search, "$options": "i"}}
        ]

    if date_from or date_to:
        date_filter = {}

        try:
            if date_from:
                date_filter["$gte"] = datetime.strptime(
                    date_from, "%Y-%m-%d"
                )

            if date_to:
                date_filter["$lt"] = datetime.strptime(
                    date_to, "%Y-%m-%d"
                ) + timedelta(days=1)

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD"
            )

        query["created_at"] = date_filter

    tickets = list(database.tickets.find(query, {"_id": 0}))

    return {
        "total": len(tickets),
        "tickets": tickets
    }




@app.get("/tickets/summary")
def get_ticket_summary():

    resolved = database.tickets.count_documents({
        "status": "Resolved"
    })

    awaiting_reply = database.tickets.count_documents({
        "status": "Awaiting Reply"
    })

    active = database.tickets.count_documents({
        "status": {
            "$in": ["Open", "In Progress", "Awaiting Reply"]
        }
    })

    return {
        "resolved": resolved,
        "awaiting_reply": awaiting_reply,
        "active": active
    }


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id},
        {"_id": 0}
    )

    if ticket is None:
        return {"message": "Ticket not found"}

    return ticket


@app.patch("/tickets/{ticket_id}/status")
def update_ticket_status(ticket_id: str, status: str):

    allowed_statuses = [
        "Open",
        "In Progress",
        "Awaiting Reply",
        "Resolved"
    ]

    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Invalid ticket status"
        )

    result = database.tickets.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": status}}
    )

    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    return {
        "message": "Ticket status updated successfully",
        "status": status
    }

@app.post("/tickets/{ticket_id}/messages")
@limiter.limit("10/minute")
def create_message(request: Request, ticket_id: str, message: Message):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    new_message = {
        "message_id": str(uuid4()),
        "ticket_id": ticket_id,
        "sender_id": message.sender_id,
        "sender_type": message.sender_type,
        "message": message.message,
        "created_at": datetime.now(timezone.utc),
        "is_read": False
    }

    database.messages.insert_one(new_message)

    return {
        "message": "Message sent successfully",
        "message_id": new_message["message_id"]
    }

@app.get("/tickets/{ticket_id}/messages")
def get_ticket_messages(ticket_id: str):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    messages = list(
        database.messages.find(
            {"ticket_id": ticket_id},
            {"_id": 0}
        ).sort("created_at", 1)
    )

    return {
        "ticket_id": ticket_id,
        "messages": messages
    }


@app.post(
    "/tickets/{ticket_id}/discord-thread",
    dependencies=[Depends(verify_discord_bot)]
)
def connect_discord_thread(
    ticket_id: str,
    discord: DiscordThread
):
    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    database.discord_threads.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "ticket_id": ticket_id,
                "guild_id": discord.guild_id,
                "channel_id": discord.channel_id,
                "thread_id": discord.thread_id,
                "created_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )

    return {
        "message": "Discord thread connected successfully"
    }


@app.post(
    "/tickets/{ticket_id}/staff-reply",
    dependencies=[Depends(verify_discord_bot)]
)
def staff_reply(ticket_id: str, reply: StaffReply):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    new_message = {
        "message_id": str(uuid4()),
        "ticket_id": ticket_id,
        "sender_id": reply.staff_id,
        "sender_type": "staff",
        "message": reply.message,
        "created_at": datetime.now(timezone.utc)
    }

    database.messages.insert_one(new_message)

    return {
        "message": "Staff reply saved successfully"
    }


@app.get(
    "/discord-threads/{thread_id}",
    dependencies=[Depends(verify_discord_bot)]
)
def get_ticket_from_discord(thread_id: str):

    discord_thread = database.discord_threads.find_one(
        {"thread_id": thread_id},
        {"_id": 0}
    )

    if discord_thread is None:
        raise HTTPException(
            status_code=404,
            detail="Discord thread not found"
        )

    return discord_thread


@app.post("/tickets/{ticket_id}/attachments")
@limiter.limit("5/minute")
def upload_attachment(request: Request, ticket_id: str, file: UploadFile = File(...)):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    extension = Path(file.filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPG and PDF files are allowed"
        )

    content = file.file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="File cannot exceed 50 MB"
        )

    valid_file = (
        (extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or
        (extension in [".jpg", ".jpeg"] and content.startswith(b"\xff\xd8\xff")) or
        (extension == ".pdf" and content.startswith(b"%PDF-"))
    )

    if not valid_file:
        raise HTTPException(
            status_code=400,
            detail="File content does not match its extension"
        )

    attachment_id = str(uuid4())
    filename = attachment_id + extension
    file_path = UPLOAD_FOLDER / filename

    file_path.write_bytes(content)

    database.attachments.insert_one({
        "attachment_id": attachment_id,
        "ticket_id": ticket_id,
        "original_filename": file.filename,
        "stored_filename": filename,
        "file_type": extension,
        "file_size": len(content),
        "created_at": datetime.now(timezone.utc)
    })

    return {
        "message": "Attachment uploaded successfully",
        "attachment_id": attachment_id
    }

@app.get("/tickets/{ticket_id}/attachments")
def get_ticket_attachments(ticket_id: str):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    attachments = list(
        database.attachments.find(
            {"ticket_id": ticket_id},
            {"_id": 0}
        )
    )

    return {
        "ticket_id": ticket_id,
        "attachments": attachments
    }


@app.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: str):

    attachment = database.attachments.find_one(
        {"attachment_id": attachment_id}
    )

    if attachment is None:
        raise HTTPException(
            status_code=404,
            detail="Attachment not found"
        )

    file_path = UPLOAD_FOLDER / attachment["stored_filename"]

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    return FileResponse(
        path=file_path,
        filename=attachment["original_filename"]
    )

@app.patch("/tickets/{ticket_id}/close")
def close_ticket(ticket_id: str):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    if ticket["status"] == "Resolved":
        raise HTTPException(
            status_code=400,
            detail="Ticket is already resolved"
        )

    database.tickets.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "status": "Resolved",
                "resolved_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )

    return {
        "message": "Ticket resolved successfully"
    }


@app.patch("/tickets/{ticket_id}/reopen")
def reopen_ticket(ticket_id: str):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    if ticket["status"] != "Resolved":
        raise HTTPException(
            status_code=400,
            detail="Only resolved tickets can be reopened"
        )

    resolved_at = datetime.fromisoformat(ticket["resolved_at"])

    if datetime.now(timezone.utc) - resolved_at > timedelta(days=7):
        raise HTTPException(
            status_code=400,
            detail="Tickets can only be reopened within 7 days"
        )

    database.tickets.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {"status": "Open"},
            "$unset": {"resolved_at": ""}
        }
    )

    return {
        "message": "Ticket reopened successfully",
        "status": "Open"
    }

@app.post("/tickets/{ticket_id}/feedback")
def submit_feedback(ticket_id: str, feedback: Feedback):

    ticket = database.tickets.find_one(
        {"ticket_id": ticket_id}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    if ticket["status"] != "Resolved":
        raise HTTPException(
            status_code=400,
            detail="Feedback can only be submitted for resolved tickets"
        )

    new_feedback = {
        "feedback_id": str(uuid4()),
        "ticket_id": ticket_id,
        "customer_id": feedback.customer_id,
        "comment": feedback.comment,
        "created_at": datetime.now(timezone.utc)
    }

    database.feedback.insert_one(new_feedback)

    return {
        "message": "Feedback submitted successfully"
    }

@app.patch("/tickets/{ticket_id}/messages/read")
def mark_messages_as_read(ticket_id: str, reader_type: str):
    if reader_type not in ["customer", "staff"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid reader type"
        )

    result = database.messages.update_many(
        {
            "ticket_id": ticket_id,
            "sender_type": {
                "$ne": reader_type
            },
            "is_read": False
        },
        {
            "$set": {
                "is_read": True
            }
        }
    )

    return {
        "message": "Messages marked as read",
        "updated_count": result.modified_count
    }