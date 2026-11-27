
"""Small pure functions (no Discord calls) so they can be unit-tested."""

import re
from datetime import datetime, timezone


def thread_name(ticket):
    """Creates a Discord thread name using the ticket number."""
    number = ticket.get("ticket_number", "Unknown")
    return f"[#{number}] {ticket['title']}"[:100]


def ticket_number_from_name(name):
    """Extracts the ticket number from a Discord thread name."""
    match = re.match(
        r"^\[#(\d+)\]",
        name or ""
    )
    return int(match.group(1)) if match else None


def initials_of(display_name):
    """Converts a staff name into initials."""
    words = [
        w for w in re.split(
            r"[\s._-]+",
            str(display_name or "Support").strip()
        ) if w
    ]

    if not words:
        words = ["Support"]

    if len(words) == 1:
        return words[0][:2].upper()

    return "".join(w[0].upper() for w in words[:3])


def strip_mentions(text):
    """Removes Discord mentions from a staff message."""
    text = re.sub(r"<@!?\d+>", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def message_id_from_footer(footer_text):
    """Extracts a message ID from an embed footer."""
    match = re.search(
        r"Message ID:\s*(\S+)",
        footer_text or ""
    )
    return match.group(1) if match else None


def event_text(ev):
    """Creates text for a ticket event."""
    kind = ev.get("type")

    if kind == "closed":
        feedback = (
            f"\n> Feedback: {ev['feedback']}"
            if ev.get("feedback") else ""
        )
        return f"🔒 Client **closed** the ticket.{feedback}"

    if kind == "resolved":
        return "✅ Client marked the ticket as **resolved**."

    if kind == "reopened":
        reason = (
            f"\n> Reason: {ev['reason']}"
            if ev.get("reason") else ""
        )
        return f"🔓 Client **reopened** the ticket.{reason}"

    if kind == "attachment":
        return "📎 Client added an attachment."

    return f"ℹ️ Ticket updated: {kind}"


def is_authorised(headers, secret):
    """Checks the shared secret header."""
    return bool(secret) and headers.get("x-shared-secret") == secret


def parse_time(value):
    """Converts a date or datetime into a timezone-aware datetime."""
    try:
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result

    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def attachment_links(attachments):
    """Converts attachments into Markdown links."""
    return "\n".join(
        f"[{a.get('fileName', 'file')}]({a.get('url', '')})"
        for a in attachments
    )
