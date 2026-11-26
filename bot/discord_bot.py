"""Everything that happens inside Discord, plus the small HTTP server the REST server calls."""
import asyncio
import json
import logging
import signal

import discord
from aiohttp import web

from . import helpers as h
from .api import TicketApi
from .routes import handle

log = logging.getLogger("posperity.bot")

BLUE = 0x3D7BFA
GREY = 0x28293D
RED = 0xF0495D


class SupportBot(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.default()      # guilds, messages, reactions
        intents.message_content = True           # also switch on in the Developer Portal
        super().__init__(
            intents=intents,
            # Only @support can be pinged by the bot, never @everyone or users
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, replied_user=False,
                roles=[discord.Object(id=config.support_role_id)],
            ),
        )
        self.config = config
        self.api = TicketApi(config.api_url, config.secret)
        self.runner = None

    @property
    def role(self):
        return f"<@&{self.config.support_role_id}>"

    # ---------- Start-up and shut-down ----------
    async def setup_hook(self):
        await self.api.start()
        # Persistent buttons: they keep working after the bot restarts
        self.add_view(TicketView(self))
        self.add_view(ReplyView(self))
        await self.start_http_server()
        try:  # Railway sends SIGTERM when it restarts the service (not available on Windows)
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.close()))
        except (NotImplementedError, AttributeError):
            pass

    async def start_http_server(self):
        handlers = {
            "create_ticket": self.create_ticket_thread,
            "client_message": self.post_client_message,
            "event": self.post_event,
            "alert": self.post_alert,
            "is_ready": self.is_ready,
        }

        async def endpoint(request):
            raw = await request.read()
            headers = {k.lower(): v for k, v in request.headers.items()}
            status, payload = await handle(request.method, request.path, headers, raw, self.config.secret, handlers)
            return web.json_response(payload, status=status)

        app = web.Application(client_max_size=1_000_000)
        app.router.add_route("*", "/{tail:.*}", endpoint)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, port=self.config.port).start()
        print(f"Bot API listening on port {self.config.port}")

    async def on_ready(self):
        print(f"Discord: logged in as {self.user}")

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
        await self.api.close()
        await super().close()

    # ---------- Finding channels and threads ----------
    def ensure_ready(self):
        if not self.is_ready():
            raise RuntimeError("Discord is not connected yet")

    async def channel_by_id(self, channel_id):
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def find_thread(self, ticket_number):
        channel = await self.channel_by_id(self.config.tickets_channel_id)
        wanted = str(ticket_number)
        for thread in await channel.guild.active_threads():
            if thread.parent_id == channel.id and h.ticket_number_from_name(thread.name) == wanted:
                return thread
        async for thread in channel.archived_threads(limit=100):
            if h.ticket_number_from_name(thread.name) == wanted:
                await thread.edit(archived=False)   # reopen it so staff can reply
                return thread
        raise LookupError(f"No Discord thread for ticket #{ticket_number}")

    def ticket_of(self, channel):
        """Ticket number if `channel` is a ticket thread under #tickets, otherwise None."""
        if (isinstance(channel, discord.Thread)
                and channel.type == discord.ChannelType.public_thread
                and channel.parent_id == self.config.tickets_channel_id):
            return h.ticket_number_from_name(channel.name)
        return None

    # ---------- REST server -> Discord ----------
    async def create_ticket_thread(self, t):
        """New ticket: alert in #tickets (pings @support), thread opened from it, details + buttons pinned."""
        self.ensure_ready()
        channel = await self.channel_by_id(self.config.tickets_channel_id)
        who = t.get("submittedBy") or "a client"
        alert = await channel.send(f"🆕 {self.role} New ticket **#{t['ticketNumber']}** from **{who}**: {t['title']}")
        thread = await alert.create_thread(name=h.thread_name(t), auto_archive_duration=10080)

        embed = discord.Embed(
            colour=BLUE,
            title=f"Ticket #{t['ticketNumber']}: {t['title']}"[:256],
            description=(t.get("description") or "—")[:4000],
            timestamp=h.parse_time(t.get("createdAt")),
        )
        client_info = (t.get("submittedBy") or "—") + (f"\n{t['email']}" if t.get("email") else "")
        embed.add_field(name="Client", value=client_info[:1024], inline=True)
        embed.add_field(name="Category", value=t.get("category") or "—", inline=True)
        embed.add_field(name="From", value=t.get("platform") or "—", inline=True)   # POS or Website
        if t.get("attachments"):
            embed.add_field(name="Attachments", value=h.attachment_links(t["attachments"])[:1000], inline=False)
        if t.get("technicalDetails"):
            details = json.dumps(t["technicalDetails"], indent=1)[:900]
            embed.add_field(name="Technical details", value=f"```{details}```", inline=False)

        top = await thread.send(
            content=(f"{self.role} **Tag the bot** (@{self.user.name}) to send a message to the client. "
                     f"Untagged messages stay internal.\n"
                     f"React {self.config.viewed_emoji} to a client message to show them it was viewed."),
            embed=embed,
            view=TicketView(self),
        )
        try:
            await top.pin()
        except discord.HTTPException:
            pass
        return {"threadId": str(thread.id)}

    async def post_client_message(self, ticket_number, m):
        """Client message: posted as an embed whose footer carries the API message id (used for 👀)."""
        self.ensure_ready()
        thread = await self.find_thread(ticket_number)
        embed = discord.Embed(
            colour=GREY,
            description=(m.get("text") or "(no text)")[:4000],
            timestamp=h.parse_time(m.get("sentAt")),
        )
        embed.set_author(name=m.get("clientName") or "Client")
        embed.set_footer(text=f"Message ID: {m.get('messageId')}")
        if m.get("attachments"):
            embed.add_field(name="Attachments", value=h.attachment_links(m["attachments"])[:1000], inline=False)
        sent = await thread.send(content=f"💬 {self.role}", embed=embed, view=ReplyView(self))
        return {"delivered": True, "discordMessageId": str(sent.id)}   # the server sets deliveredAt -> ✓ for the client

    async def post_event(self, ticket_number, ev):
        """Client closed / resolved / reopened the ticket, or added a file."""
        self.ensure_ready()
        thread = await self.find_thread(ticket_number)
        await thread.send(f"{h.event_text(ev)} {self.role}")
        if ev.get("type") in ("closed", "resolved"):
            await thread.edit(archived=True)
        return {"ok": True}

    async def post_alert(self, a):
        """Error from the server's middleware: nothing is lost, staff follow up."""
        self.ensure_ready()
        channel = await self.channel_by_id(self.config.alerts_channel_id)
        embed = discord.Embed(
            colour=RED,
            title=f"⚠️ {a.get('errorRef') or 'Error'} on {a.get('route') or 'unknown route'}"[:256],
            description=f"```{str(a.get('log') or 'No log')[:3900]}```",
            timestamp=h.parse_time(a.get("timestamp")),
        )
        embed.add_field(name="Customer", value=str(a.get("customer") or "unknown")[:1000], inline=False)
        await channel.send(f"{self.role} please follow up manually", embed=embed)
        return {"ok": True}

    # ---------- Discord -> REST server ----------
    async def on_message(self, msg):
        """A staff message is sent to the client ONLY when it tags the bot."""
        if msg.author.bot:
            return
        ticket_number = self.ticket_of(msg.channel)
        if not ticket_number or not any(u.id == self.user.id for u in msg.mentions):
            return   # untagged = internal discussion
        text = h.strip_mentions(msg.content)
        if not text and not msg.attachments:
            return
        try:
            await self.api.send_staff_message(ticket_number, {
                "staffInitials": h.initials_of(msg.author.display_name),
                "text": text,
                "attachments": [{"fileName": a.filename, "url": a.url, "size": a.size} for a in msg.attachments],
                "discordMessageId": str(msg.id),
            })
            await msg.add_reaction("✅")
        except Exception:
            log.exception("Could not send staff message")
            await msg.add_reaction("❌")
            await msg.reply("Could not send this to the client. Try again in a moment.")

    async def on_raw_reaction_add(self, payload):
        """👀 on a client message = "viewed by support" (Discord does not tell bots when a message is read)."""
        if str(payload.emoji) != self.config.viewed_emoji or payload.user_id == self.user.id:
            return
        if payload.member and payload.member.bot:
            return
        try:
            channel = await self.channel_by_id(payload.channel_id)
            ticket_number = self.ticket_of(channel)
            if not ticket_number:
                return
            message = await channel.fetch_message(payload.message_id)
            if message.author.id != self.user.id or not message.embeds:
                return
            message_id = h.message_id_from_footer(message.embeds[0].footer.text)
            if not message_id:
                return
            name = payload.member.display_name if payload.member else str(payload.user_id)
            await self.api.mark_viewed(ticket_number, message_id, h.initials_of(name))
        except Exception as err:
            print("Could not mark as viewed:", err)


# ---------- Buttons and pop-up forms ----------
async def report_error(interaction, error):
    log.error("Interaction failed", exc_info=error)
    text = f"❌ That didn't work: {error}"[:2000]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


class MessageModal(discord.ui.Modal):
    """Pop-up form with one text box. `kind` is "reply", "info" or "resolve"."""

    def __init__(self, bot, kind, title, label="Message to the client"):
        super().__init__(title=title[:45], custom_id=f"{kind}_modal")
        self.bot = bot
        self.kind = kind
        self.text = discord.ui.TextInput(label=label, style=discord.TextStyle.paragraph,
                                         required=True, max_length=2000)
        self.add_item(self.text)

    async def on_submit(self, interaction):
        ticket_number = self.bot.ticket_of(interaction.channel)
        initials = h.initials_of(interaction.user.display_name)
        text = self.text.value.strip()
        api = self.bot.api
        await interaction.response.defer(thinking=True)   # gives the API more than Discord's 3 seconds

        if self.kind == "reply":
            await api.send_staff_message(ticket_number, {"staffInitials": initials, "text": text})
            await interaction.followup.send(f"**{initials} → client:** {text}"[:2000])

        elif self.kind == "info":
            await api.send_staff_message(ticket_number, {"staffInitials": initials, "text": text, "requestInfo": True})
            await api.set_status(ticket_number, "Waiting for customer", initials)   # client gets the pop-up
            await interaction.followup.send(f"❓ **{initials} asked the client:** {text}"[:2000])

        elif self.kind == "resolve":
            await api.send_staff_message(ticket_number, {"staffInitials": initials, "text": text})
            await api.set_status(ticket_number, "Resolved", initials)
            await interaction.followup.send(f"✅ Ticket #{ticket_number} resolved by **{initials}**.")
            await interaction.channel.edit(archived=True)

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class TicketButtonsBase(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)   # buttons never expire
        self.bot = bot

    async def interaction_check(self, interaction):
        if self.bot.ticket_of(interaction.channel):
            return True
        await interaction.response.send_message("This only works inside a ticket thread.", ephemeral=True)
        return False

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)

    async def open_reply(self, interaction):
        number = self.bot.ticket_of(interaction.channel)
        await interaction.response.send_modal(MessageModal(self.bot, "reply", f"Reply to ticket #{number}"))


class TicketView(TicketButtonsBase):
    """The four buttons pinned at the top of every ticket thread."""

    @discord.ui.button(label="Reply to client", style=discord.ButtonStyle.primary, custom_id="reply")
    async def reply(self, interaction, button):
        await self.open_reply(interaction)

    @discord.ui.button(label="Request more info", style=discord.ButtonStyle.secondary, custom_id="request_info")
    async def request_info(self, interaction, button):
        await interaction.response.send_modal(
            MessageModal(self.bot, "info", "Request more information", "What do you need from the client?"))

    @discord.ui.button(label="Mark in progress", style=discord.ButtonStyle.secondary, custom_id="in_progress")
    async def in_progress(self, interaction, button):
        number = self.bot.ticket_of(interaction.channel)
        initials = h.initials_of(interaction.user.display_name)
        await interaction.response.defer(thinking=True)
        await self.bot.api.set_status(number, "In progress", initials)
        await interaction.followup.send(f"🔧 **{initials}** marked #{number} **In progress**.")

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, custom_id="resolve")
    async def resolve(self, interaction, button):
        await interaction.response.send_modal(
            MessageModal(self.bot, "resolve", "Resolve – final message to the client"))


class ReplyView(TicketButtonsBase):
    """The Reply button under each client message."""

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.primary, custom_id="reply_message")
    async def reply(self, interaction, button):
        await self.open_reply(interaction)
