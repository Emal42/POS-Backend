
import logging

import discord

from app.services.ticket_service import (
    get_ticket,
    create_ticket_message,
    update_ticket_status,
    save_discord_thread
)

from . import helpers as h


log = logging.getLogger("posperity.bot")

BLUE = 0x3D7BFA
GREY = 0x28293D
RED = 0xF0495D


class SupportBot(discord.Client):

    def __init__(self, config):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                replied_user=False,
                roles=[discord.Object(id=config.support_role_id)]
            )
        )

        self.config = config

    @property
    def role(self):
        return f"<@&{self.config.support_role_id}>"

    # ---------- Start-up and shut-down ----------

    async def setup_hook(self):
        self.add_view(TicketView(self))
        self.add_view(ReplyView(self))

    async def on_ready(self):
        print(f"Discord: logged in as {self.user}")

    # ---------- Finding channels and threads ----------

    def ensure_ready(self):
        if not self.is_ready():
            raise RuntimeError("Discord is not connected yet")

    async def channel_by_id(self, channel_id):
        return (
            self.get_channel(channel_id)
            or await self.fetch_channel(channel_id)
        )

    async def find_thread(self, ticket_id):
        channel = await self.channel_by_id(
            self.config.tickets_channel_id
        )

        for thread in await channel.guild.active_threads():
            if (
                thread.parent_id == channel.id
                and h.ticket_number_from_name(thread.name) == ticket_id
            ):
                return thread

        async for thread in channel.archived_threads(limit=100):
            if h.ticket_number_from_name(thread.name) == ticket_id:
                await thread.edit(archived=False)
                return thread

        raise LookupError(
            f"No Discord thread for ticket {ticket_id}"
        )

    def ticket_of(self, channel):
        if (
            isinstance(channel, discord.Thread)
            and channel.type == discord.ChannelType.public_thread
            and channel.parent_id == self.config.tickets_channel_id
        ):
            return h.ticket_number_from_name(channel.name)

        return None

    # ---------- Backend to Discord ----------

    async def create_ticket_thread(self, ticket):
        """Creates a Discord thread for a new support ticket."""

        self.ensure_ready()

        channel = await self.channel_by_id(
            self.config.tickets_channel_id
        )

        ticket_id = ticket["ticket_id"]
        customer_id = ticket["customer_id"]
        title = ticket["title"]

        alert = await channel.send(
            f"🆕 {self.role} New ticket from customer "
            f"**{customer_id}**: **{title}**"
        )

        thread = await alert.create_thread(
            name=h.thread_name(ticket),
            auto_archive_duration=10080
        )

        embed = discord.Embed(
            colour=BLUE,
            title=f"Ticket: {title}"[:256],
            description=ticket.get("description", "—")[:4000],
            timestamp=h.parse_time(ticket.get("created_at"))
        )

        embed.add_field(
            name="Customer ID",
            value=str(customer_id)[:1024],
            inline=True
        )

        embed.add_field(
            name="Category",
            value=ticket.get("category", "—"),
            inline=True
        )

        embed.add_field(
            name="Source",
            value=ticket.get("source", "—"),
            inline=True
        )

        save_discord_thread(
            ticket_id,
            thread.guild.id,
            channel.id,
            thread.id
        )

        top = await thread.send(
            content=(
                f"{self.role} Tag the bot to send a message "
                "to the customer. Untagged messages stay internal."
            ),
            embed=embed,
            view=TicketView(self)
        )

        try:
            await top.pin()
        except discord.HTTPException:
            pass

        return {"threadId": str(thread.id)}

    async def post_client_message(self, ticket_id, message):
        """Displays a customer message in its Discord ticket thread."""

        self.ensure_ready()

        thread = await self.find_thread(ticket_id)

        ticket = get_ticket(ticket_id)

        customer_name = (
            str(ticket.get("customer_id", "Customer"))
            if ticket else "Customer"
        )

        embed = discord.Embed(
            colour=GREY,
            description=(
                message.get("message")
                or message.get("text")
                or "(no text)"
            )[:4000],
            timestamp=h.parse_time(
                message.get("created_at")
                or message.get("sentAt")
            )
        )

        embed.set_author(name=customer_name[:256])

        attachments = message.get("attachments") or []

        if attachments:
            embed.add_field(
                name="Attachments",
                value=h.attachment_links(attachments)[:1000],
                inline=False
            )

        await thread.send(
            content=f"💬 {self.role}",
            embed=embed,
            view=ReplyView(self)
        )

        return {"delivered": True}

    async def post_event(self, ticket_id, event):
        """Displays ticket events in Discord."""

        self.ensure_ready()

        thread = await self.find_thread(ticket_id)

        await thread.send(
            f"{h.event_text(event)} {self.role}"
        )

        if event.get("type") in ("closed", "resolved"):
            await thread.edit(archived=True)

        return {"ok": True}

    async def post_alert(self, alert):
        """Sends backend error alerts to the Discord alerts channel."""

        self.ensure_ready()

        channel = await self.channel_by_id(
            self.config.alerts_channel_id
        )

        embed = discord.Embed(
            colour=RED,
            title=(
                f"⚠️ {alert.get('errorRef') or 'Error'} "
                f"on {alert.get('route') or 'unknown route'}"
            )[:256],
            description=(
                f"```{str(alert.get('log') or 'No log')[:3900]}```"
            ),
            timestamp=h.parse_time(alert.get("timestamp"))
        )

        embed.add_field(
            name="Customer",
            value=str(alert.get("customer") or "unknown")[:1000],
            inline=False
        )

        await channel.send(
            f"{self.role} Please follow up manually",
            embed=embed
        )

        return {"ok": True}

    # ---------- Discord to backend ----------

    async def on_message(self, message):
        """Sends staff replies to the customer when the bot is mentioned."""

        if message.author.bot:
            return

        ticket_id = self.ticket_of(message.channel)

        if not ticket_id:
            return

        if not any(
            user.id == self.user.id
            for user in message.mentions
        ):
            return

        text = h.strip_mentions(message.content)

        if not text and not message.attachments:
            return

        try:
            create_ticket_message(
                ticket_id=ticket_id,
                sender_id=message.author.id,
                sender_type="staff",
                message=text,
                staff_name=h.initials_of(
                    message.author.display_name
                ),
                attachments=[
                    {
                        "fileName": attachment.filename,
                        "url": attachment.url,
                        "size": attachment.size
                    }
                    for attachment in message.attachments
                ]
            )

            await message.add_reaction("✅")

        except Exception:
            log.exception("Could not save staff message")

            await message.add_reaction("❌")

            await message.reply(
                "Could not send this to the customer. "
                "Try again in a moment."
            )


# ---------- Error handling ----------

async def report_error(interaction, error):
    log.error("Interaction failed", exc_info=error)

    text = f"❌ That didn't work: {error}"[:2000]

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                text,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                text,
                ephemeral=True
            )

    except discord.HTTPException:
        pass


# ---------- Reply and ticket forms ----------

class MessageModal(discord.ui.Modal):

    def __init__(
        self,
        bot,
        kind,
        title,
        label="Message to the customer"
    ):
        super().__init__(
            title=title[:45],
            custom_id=f"{kind}_modal"
        )

        self.bot = bot
        self.kind = kind

        self.text = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )

        self.add_item(self.text)

    async def on_submit(self, interaction):
        ticket_id = self.bot.ticket_of(interaction.channel)

        if not ticket_id:
            await interaction.response.send_message(
                "Ticket not found.",
                ephemeral=True
            )
            return

        initials = h.initials_of(
            interaction.user.display_name
        )

        text = self.text.value.strip()

        await interaction.response.defer(thinking=True)

        try:
            if self.kind == "reply":
                create_ticket_message(
                    ticket_id,
                    interaction.user.id,
                    "staff",
                    text,
                    staff_name=initials
                )

                await interaction.followup.send(
                    f"**{initials} → customer:** {text}"
                )

            elif self.kind == "info":
                create_ticket_message(
                    ticket_id,
                    interaction.user.id,
                    "staff",
                    text,
                    staff_name=initials
                )

                update_ticket_status(
                    ticket_id,
                    "Awaiting Reply"
                )

                await interaction.followup.send(
                    f"❓ **{initials} asked the customer:** {text}"
                )

            elif self.kind == "resolve":
                create_ticket_message(
                    ticket_id,
                    interaction.user.id,
                    "staff",
                    text,
                    staff_name=initials
                )

                update_ticket_status(
                    ticket_id,
                    "Resolved"
                )

                await interaction.followup.send(
                    f"✅ Ticket resolved by **{initials}**."
                )

                await interaction.channel.edit(archived=True)

        except Exception as error:
            await interaction.followup.send(
                f"Could not complete the action: {error}",
                ephemeral=True
            )

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


# ---------- Ticket buttons ----------

class TicketButtonsBase(discord.ui.View):

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction):
        if self.bot.ticket_of(interaction.channel):
            return True

        await interaction.response.send_message(
            "This only works inside a ticket thread.",
            ephemeral=True
        )

        return False

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)

    async def open_reply(self, interaction):
        ticket_id = self.bot.ticket_of(interaction.channel)

        await interaction.response.send_modal(
            MessageModal(
                self.bot,
                "reply",
                f"Reply to ticket {ticket_id}"
            )
        )


class TicketView(TicketButtonsBase):
    """Buttons displayed at the top of each ticket thread."""

    @discord.ui.button(
        label="Reply to client",
        style=discord.ButtonStyle.primary,
        custom_id="reply"
    )
    async def reply(self, interaction, button):
        await self.open_reply(interaction)

    @discord.ui.button(
        label="Request more info",
        style=discord.ButtonStyle.secondary,
        custom_id="request_info"
    )
    async def request_info(self, interaction, button):
        await interaction.response.send_modal(
            MessageModal(
                self.bot,
                "info",
                "Request more information",
                "What do you need from the customer?"
            )
        )

    @discord.ui.button(
        label="Mark in progress",
        style=discord.ButtonStyle.secondary,
        custom_id="in_progress"
    )
    async def in_progress(self, interaction, button):
        ticket_id = self.bot.ticket_of(interaction.channel)

        initials = h.initials_of(
            interaction.user.display_name
        )

        await interaction.response.defer(thinking=True)

        try:
            update_ticket_status(
                ticket_id,
                "In Progress"
            )

            await interaction.followup.send(
                f"🔧 **{initials}** marked the ticket as **In Progress**."
            )

        except Exception as error:
            await interaction.followup.send(
                f"Could not update ticket: {error}",
                ephemeral=True
            )

    @discord.ui.button(
        label="Resolve",
        style=discord.ButtonStyle.success,
        custom_id="resolve"
    )
    async def resolve(self, interaction, button):
        await interaction.response.send_modal(
            MessageModal(
                self.bot,
                "resolve",
                "Resolve – final message to the customer"
            )
        )


class ReplyView(TicketButtonsBase):
    """Reply button displayed underneath customer messages."""

    @discord.ui.button(
        label="Reply",
        style=discord.ButtonStyle.primary,
        custom_id="reply_message"
    )
    async def reply(self, interaction, button):
        await self.open_reply(interaction)
