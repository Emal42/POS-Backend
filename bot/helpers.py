"""Small pure functions (no Discord calls) so they can be unit-tested."""
import re
from datetime import datetime, timezone


def thread_name(ticket):
    """Thread name shown in Discord: "[#1043] Receipt printer offline – Goober time" (max 100 chars)."""
    who = ticket.get("submittedBy") or "Client"
    return f"[#{ticket['ticketNumber']}] {ticket['title']} – {who}"[:100]


def ticket_number_from_name(name):
    """Reads the ticket number back out of a thread name, or None."""
    match = re.match(r"^\[#(\d+)\]", name or "")
    return match.group(1) if match else None


def initials_of(display_name):
    """Staff are shown to clients by initials only: "Sam King" -> "SK", "mt" -> "MT"."""
    words = [w for w in re.split(r"[\s._-]+", str(display_name or "Support").strip()) if w]
    if not words:
        words = ["Support"]
    if len(words) == 1:
        return words[0][:2].upper()
    return "".join(w[0].upper() for w in words[:3])


def strip_mentions(text):
    """Removes the bot mention(s) from a staff message so only the reply text is sent to the client."""
    text = re.sub(r"<@!?\d+>", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def message_id_from_footer(footer_text):
    """The API message id is stored in the embed footer of each client message: "Message ID: abc123"."""
    match = re.search(r"Message ID:\s*(\S+)", footer_text or "")
    return match.group(1) if match else None


def event_text(ev):
    """Text posted in the thread for a client change."""
    kind = ev.get("type")
    if kind == "closed":
        feedback = f"\n> Feedback: {ev['feedback']}" if ev.get("feedback") else ""
        return f"🔒 Client **closed** the ticket.{feedback}"
    if kind == "resolved":
        return "✅ Client marked the ticket as **resolved**."
    if kind == "reopened":
        reason = f"\n> Reason: {ev['reason']}" if ev.get("reason") else ""
        return f"🔓 Client **reopened** the ticket.{reason}"
    if kind == "attachment":
        return "📎 Client added an attachment."
    return f"ℹ️ Ticket updated: {kind}"


def is_authorised(headers, secret):
    """Checks the shared secret header on calls between the REST server and the bot.
    `headers` must have lower-case keys."""
    return bool(secret) and headers.get("x-shared-secret") == secret


def parse_time(value):
    """ISO date from the server ("2026-11-26T09:32:00Z") -> datetime. Falls back to now."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def attachment_links(attachments):
    """[{fileName, url}] -> Markdown links, one per line."""
    return "\n".join(f"[{a.get('fileName', 'file')}]({a.get('url', '')})" for a in attachments)
