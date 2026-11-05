"""The control panel posted in each temp VC's text chat."""

from __future__ import annotations

import logging

import discord

from ..constants import (
    LOCKED_LABEL,
    NO_USER_LIMIT,
    RENAME_LABEL,
    TIMER_ALREADY_STARTED_MESSAGE,
    TIMER_CHOICES,
    TIMER_PLACEHOLDER,
    UNLOCKED_LABEL,
    USER_LIMIT_LABEL,
)
from ..errors import API_ERRORS
from ..services.channel_manager import ChannelManager
from ..state import TempVCSession
from .modals import RenameModal, UserLimitModal
from .owner_only import OwnerOnlyView

log = logging.getLogger(__name__)


def _timer_options(selected_minutes: int) -> list[discord.SelectOption]:
    """Build the dropdown's choices, marking the selected one."""
    return [
        discord.SelectOption(label=f"{minutes} minutes", value=str(minutes), default=minutes == selected_minutes)
        for minutes in TIMER_CHOICES
    ]


class ControlPanelView(OwnerOnlyView):
    """The owner's rename, user limit, lock and timer controls."""

    def __init__(self, channels: ChannelManager, session: TempVCSession) -> None:
        # timeout=None keeps the controls working for as long as the channel exists.
        super().__init__(session.owner_id, timeout=None)
        self.channels = channels
        self.session = session
        self._sync_with_session()

    def _sync_with_session(self) -> None:
        """Make the lock button and dropdown match the session's current state."""
        locked = self.session.locked
        self.lock_button.label = LOCKED_LABEL if locked else UNLOCKED_LABEL
        self.lock_button.emoji = "🔒" if locked else "🔓"
        self.lock_button.style = discord.ButtonStyle.danger if locked else discord.ButtonStyle.success
        self.timer_select.options = _timer_options(self.session.timer_minutes)
        self.timer_select.disabled = self.session.timer_started

    def build_embed(self) -> discord.Embed:
        """Build the panel's embed from the session's current state."""
        session = self.session
        limit = session.requested_limit
        embed = discord.Embed(
            title="🎛️ Channel controls",
            description=f"Owner: <@{session.owner_id}>\nOnly the owner can use these controls.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="User limit", value="No limit" if limit == NO_USER_LIMIT else str(limit))
        embed.add_field(name="Access", value="Locked" if session.locked else "Open")
        embed.add_field(name="Timer length", value=f"{session.timer_minutes} minutes")
        return embed

    async def post(self, channel: discord.VoiceChannel) -> None:
        """Post the panel in the channel's text chat, and tag the owner so they see it."""
        owner_id = self.session.owner_id
        message = await channel.send(
            f"<@{owner_id}> here are the controls for your channel.",
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(owner_id)]),
        )
        self.session.panel_message_id = message.id

    async def refresh_message(self) -> None:
        """Update the posted panel after something outside it changed."""
        channel = self.channels.get_channel(self.session)
        if channel is None or self.session.panel_message_id is None:
            return
        self._sync_with_session()
        try:
            message = channel.get_partial_message(self.session.panel_message_id)
            await message.edit(embed=self.build_embed(), view=self)
        except API_ERRORS as error:
            log.warning("Could not update the control panel in channel %s: %s", channel.id, error)

    @discord.ui.button(label=RENAME_LABEL, emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the rename form."""
        channel = self.channels.require_channel(self.session)
        await interaction.response.send_modal(RenameModal(self, channel.name))

    @discord.ui.button(label=USER_LIMIT_LABEL, emoji="👥", style=discord.ButtonStyle.primary, row=0)
    async def limit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the user limit form."""
        await interaction.response.send_modal(UserLimitModal(self))

    @discord.ui.button(label=UNLOCKED_LABEL, row=0)
    async def lock_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Switch between locked and unlocked."""
        # Acknowledge first: Discord fails any click not answered within 3 seconds.
        await interaction.response.defer()
        await self.channels.set_locked(self.session, not self.session.locked)
        self._sync_with_session()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.select(placeholder=TIMER_PLACEHOLDER, options=_timer_options(TIMER_CHOICES[0]), row=1)
    async def timer_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Choose the timer's length. This only works before the timer starts."""
        # The dropdown is disabled when the timer starts, but someone's screen can
        # be a moment behind, so the bot checks again here.
        if self.session.timer_started:
            await interaction.response.send_message(TIMER_ALREADY_STARTED_MESSAGE, ephemeral=True)
            return
        self.session.timer_minutes = int(select.values[0])
        self._sync_with_session()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
