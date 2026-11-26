
import logging
import os
import secrets
import shutil
import re

from app.auth import get_current_customer
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Header,
    UploadFile,
    File,
    Request
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.database import check_database, database
from app.services.ticket_service import create_ticket
from app.bot.manager import start_bot, stop_bot, get_bot


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("posperity.api")

limiter = Limiter(key_func=get_remote_address)

MAX_FILE_SIZE = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".pdf"]

UPLOAD_FOLDER = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)


# --------------------------------------------------
# FastAPI and Discord bot lifecycle
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_bot()

    try:
        yield
    finally:
        await stop_bot()


app = FastAPI(lifespan=lifespan)

app.state.limiter = limiter

app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler
)


# --------------------------------------------------
# Request models
# --------------------------------------------------

class Ticket(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    category: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=2000)
    source: Literal["main_pos", "sales_website"]


class Message(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class Feedback(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------
# General endpoints
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "POSperity Support API is running"
    }


@app.get("/test-database")
def test_database():
    if os.getenv("AUTH_MODE") == "development":
        if check_database():
            return {
                "message": "MongoDB connection successful!"
            }

        raise HTTPException(
            status_code=503,
            detail="MongoDB connection failed"
        )

    raise HTTPException(
        status_code=404,
        detail="Not found"
    )


# --------------------------------------------------
# Ticket creation
# --------------------------------------------------

@app.post("/tickets")
@limiter.limit("5/minute")
async def create_new_ticket(
    request: Request,
    ticket: Ticket,
    current_customer: dict = Depends(get_current_customer)
):
    ticket_data = ticket.model_dump()

    # Get customer ID from authentication
    ticket_data["customer_id"] = current_customer["user_id"]

    new_ticket = create_ticket(ticket_data)

    discord_notification = "pending"

    try:
        bot = get_bot()

        if bot is None or not bot.is_ready():
            raise RuntimeError("Discord bot is not ready")

        await bot.create_ticket_thread(new_ticket)

        discord_notification = "sent"

    except Exception:
        logger.exception(
            "Ticket created, but Discord notification failed: %s",
            new_ticket["ticket_id"]
        )

        discord_notification = "failed"

    return {
        "message": "Ticket created successfully",
        "ticket_id": new_ticket["ticket_id"],
        "discord_notification": discord_notification
    }


# --------------------------------------------------
# Get and filter tickets
# --------------------------------------------------

@app.get("/tickets")
def get_tickets(
    status: str = None,
    source: str = None,
    search: str = None,
    date_from: str = None,
    date_to: str = None,
    current_customer: dict = Depends(get_current_customer)
):
    # Customers can only retrieve their own tickets
    query = {
        "customer_id": current_customer["user_id"]
    }

    if status:
        query["status"] = status

    if source:
        query["source"] = source

    if search:
        safe_search = re.escape(search)

        query["$or"] = [
            {
                "ticket_id": {
                    "$regex": safe_search,
                    "$options": "i"
                }
            },
            {
                "title": {
                    "$regex": safe_search,
                    "$options": "i"
                }
            }
        ]

    if date_from or date_to:
        date_filter = {}

        try:
            if date_from:
                date_filter["$gte"] = datetime.strptime(
                    date_from,
                    "%Y-%m-%d"
                )

            if date_to:
                date_filter["$lt"] = datetime.strptime(
                    date_to,
                    "%Y-%m-%d"
                ) + timedelta(days=1)

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD"
            )

        query["created_at"] = date_filter

    tickets = list(
        database.tickets.find(
            query,
            {"_id": 0}
        ).sort("created_at", -1)
    )

    return {
        "total": len(tickets),
        "tickets": tickets
    }


# --------------------------------------------------
# Ticket summary
# --------------------------------------------------

@app.get("/tickets/summary")
def get_ticket_summary(
    current_customer: dict = Depends(get_current_customer)
):
    customer_id = current_customer["user_id"]

    resolved = database.tickets.count_documents({
        "customer_id": customer_id,
        "status": "Resolved"
    })

    awaiting_reply = database.tickets.count_documents({
        "customer_id": customer_id,
        "status": "Awaiting Reply"
    })

    active = database.tickets.count_documents({
        "customer_id": customer_id,
        "status": {
            "$in": [
                "Open",
                "In Progress"
            ]
        }
    })

    return {
        "resolved": resolved,
        "awaiting_reply": awaiting_reply,
        "active": active
    }


# --------------------------------------------------
# Get one ticket
# --------------------------------------------------

@app.get("/tickets/{ticket_id}")
def get_ticket_details(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one(
        {
            "ticket_id": ticket_id,
            "customer_id": current_customer["user_id"]
        },
        {"_id": 0}
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    return ticket


# --------------------------------------------------
# Create a ticket message
# --------------------------------------------------

@app.post("/tickets/{ticket_id}/messages")
@limiter.limit("10/minute")
async def create_message(
    request: Request,
    ticket_id: str,
    message: Message,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    new_message = {
        "message_id": str(uuid4()),
        "ticket_id": ticket_id,
        "sender_id": current_customer["user_id"],
        "sender_type": "customer",
        "message": message.message,
        "created_at": datetime.now(timezone.utc),
        "is_read": False
    }

    database.messages.insert_one(new_message)

    discord_notification = "failed"

    try:
        bot = get_bot()

        if bot is None or not bot.is_ready():
            raise RuntimeError("Discord bot is not ready")

        await bot.post_client_message(
            ticket_id,
            new_message
        )

        discord_notification = "sent"

    except Exception:
        logger.exception(
            "Customer message saved, but Discord notification failed"
        )

    return {
        "message": "Message sent successfully",
        "message_id": new_message["message_id"],
        "discord_notification": discord_notification
    }


# --------------------------------------------------
# Get ticket messages
# --------------------------------------------------

@app.get("/tickets/{ticket_id}/messages")
def get_ticket_messages(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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


# --------------------------------------------------
# Upload ticket attachment
# --------------------------------------------------

@app.post("/tickets/{ticket_id}/attachments")
@limiter.limit("5/minute")
def upload_attachment(
    request: Request,
    ticket_id: str,
    file: UploadFile = File(...),
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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
        (
            extension == ".png"
            and content.startswith(b"\x89PNG\r\n\x1a\n")
        )
        or (
            extension in [".jpg", ".jpeg"]
            and content.startswith(b"\xff\xd8\xff")
        )
        or (
            extension == ".pdf"
            and content.startswith(b"%PDF-")
        )
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


# --------------------------------------------------
# Get ticket attachments
# --------------------------------------------------

@app.get("/tickets/{ticket_id}/attachments")
def get_ticket_attachments(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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


# --------------------------------------------------
# Download attachment
# --------------------------------------------------

@app.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    attachment = database.attachments.find_one(
        {"attachment_id": attachment_id}
    )

    if attachment is None:
        raise HTTPException(
            status_code=404,
            detail="Attachment not found"
        )

    ticket = database.tickets.find_one({
        "ticket_id": attachment["ticket_id"],
        "customer_id": current_customer["user_id"]
    })

    if ticket is None:
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


# --------------------------------------------------
# Close ticket
# --------------------------------------------------

@app.patch("/tickets/{ticket_id}/close")
def close_ticket(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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
        {
            "ticket_id": ticket_id,
            "customer_id": current_customer["user_id"]
        },
        {
            "$set": {
                "status": "Resolved",
                "resolved_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }
        }
    )

    return {
        "message": "Ticket resolved successfully"
    }


# --------------------------------------------------
# Reopen ticket
# --------------------------------------------------

@app.patch("/tickets/{ticket_id}/reopen")
def reopen_ticket(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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

    resolved_at = datetime.fromisoformat(
        ticket["resolved_at"]
    )

    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(
            tzinfo=timezone.utc
        )

    if (
        datetime.now(timezone.utc) - resolved_at
        > timedelta(days=7)
    ):
        raise HTTPException(
            status_code=400,
            detail="Tickets can only be reopened within 7 days"
        )

    database.tickets.update_one(
        {
            "ticket_id": ticket_id,
            "customer_id": current_customer["user_id"]
        },
        {
            "$set": {
                "status": "Open"
            },
            "$unset": {
                "resolved_at": ""
            }
        }
    )

    return {
        "message": "Ticket reopened successfully",
        "status": "Open"
    }


# --------------------------------------------------
# Submit feedback
# --------------------------------------------------

@app.post("/tickets/{ticket_id}/feedback")
def submit_feedback(
    ticket_id: str,
    feedback: Feedback,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

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
        "customer_id": current_customer["user_id"],
        "comment": feedback.comment,
        "created_at": datetime.now(timezone.utc)
    }

    database.feedback.insert_one(new_feedback)

    return {
        "message": "Feedback submitted successfully"
    }


# --------------------------------------------------
# Customer read tracking
# --------------------------------------------------

@app.patch("/tickets/{ticket_id}/messages/read")
def mark_messages_as_read(
    ticket_id: str,
    current_customer: dict = Depends(get_current_customer)
):
    ticket = database.tickets.find_one({
        "ticket_id": ticket_id,
        "customer_id": current_customer["user_id"]
    })

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found"
        )

    result = database.messages.update_many(
        {
            "ticket_id": ticket_id,
            "sender_type": "staff",
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
