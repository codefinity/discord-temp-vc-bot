"""The message in a new channel's chat that asks the owner to name it."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from ..constants import NAME_BUTTON_LABEL, NAME_FORM_TITLE, NAME_PROMPT_MESSAGE
from ..errors import API_ERRORS
from .modals import RenameModal
from .owner_only import OwnerOnlyView

if TYPE_CHECKING:
    from ..services.channel_manager import ChannelManager
    from ..state import TempVCSession

log = logging.getLogger(__name__)


class NamePromptView(OwnerOnlyView):
    """A button that opens the naming form straight away. Only the owner can use it."""

    def __init__(self, channels: ChannelManager, session: TempVCSession) -> None:
        # timeout=None: the naming deadline decides how long this stays, not discord.py.
        super().__init__(session.owner_id, timeout=None)
        self.channels = channels
        self.session = session
        self.message: discord.Message | None = None

    async def post(self, channel: discord.VoiceChannel, minutes: float, deadline: datetime) -> None:
        """Post the prompt in the channel's text chat, and tag the owner so they see it."""
        owner_id = self.session.owner_id
        text = NAME_PROMPT_MESSAGE.format(
            mention=f"<@{owner_id}>",
            minutes=f"{minutes:g}",
            # A fixed time, not a live countdown. See _closing_time in timer.py.
            deadline=f"by {discord.utils.format_dt(deadline, 'T')}",
        )
        self.message = await channel.send(
            text, view=self, allowed_mentions=discord.AllowedMentions(users=[discord.Object(owner_id)])
        )

    async def withdraw(self) -> None:
        """Delete the prompt once the channel has a name."""
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.delete()
        except API_ERRORS as error:
            log.warning("Could not delete the naming prompt in channel %s: %s", self.session.channel_id, error)

    @discord.ui.button(label=NAME_BUTTON_LABEL, emoji="✏️", style=discord.ButtonStyle.primary)
    async def name_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the naming form. The box starts empty, so a name has to be typed."""
        await interaction.response.send_modal(RenameModal(self.channels, self.session, title=NAME_FORM_TITLE))
