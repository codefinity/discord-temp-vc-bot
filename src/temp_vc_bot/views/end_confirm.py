"""The private "are you sure?" shown when the owner ends their channel early."""

from __future__ import annotations

import logging

import discord

from ..constants import (
    END_CANCEL_LABEL,
    END_CANCELLED_MESSAGE,
    END_CONFIRM_LABEL,
    END_CONFIRM_SECONDS,
    END_EXPIRED_MESSAGE,
    ENDING_MESSAGE,
)
from ..errors import API_ERRORS
from ..services.channel_manager import ChannelManager
from ..state import TempVCSession
from .owner_only import OwnerOnlyView

log = logging.getLogger(__name__)


class EndConfirmView(OwnerOnlyView):
    """Asks the owner to confirm before their channel is deleted for everyone in it.

    A single click on the panel would be too easy to hit by mistake, and it can't
    be undone, so the panel's button only opens this question.
    """

    def __init__(self, channels: ChannelManager, session: TempVCSession, question: discord.Interaction) -> None:
        super().__init__(session.owner_id, timeout=END_CONFIRM_SECONDS)
        self.channels = channels
        self.session = session
        # The click that asked this question. The bot edits the question through it
        # if the owner doesn't answer, so the stale buttons don't linger.
        self._question = question

    @discord.ui.button(label=END_CONFIRM_LABEL, emoji="🛑", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Delete the channel now."""
        self.stop()
        try:
            # Reply first: once the channel is deleted, there's nothing left to reply in.
            await interaction.response.edit_message(content=ENDING_MESSAGE, view=None)
        finally:
            # The owner has confirmed, so the channel closes even if the reply failed.
            await self.channels.close_session(self.session, "the owner ended it early")

    @discord.ui.button(label=END_CANCEL_LABEL, style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Leave the channel as it is."""
        self.stop()
        await interaction.response.edit_message(content=END_CANCELLED_MESSAGE, view=None)

    async def on_timeout(self) -> None:
        """Replace the unanswered question, so its buttons can't be clicked once they've stopped working."""
        try:
            await self._question.edit_original_response(content=END_EXPIRED_MESSAGE, view=None)
        except API_ERRORS as error:
            # Most likely the channel was deleted meanwhile, taking the question with it.
            log.debug("Could not update the end-channel question in channel %s: %s", self.session.channel_id, error)
