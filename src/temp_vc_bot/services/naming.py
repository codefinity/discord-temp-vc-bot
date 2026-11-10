"""Make the owner name each new channel, or delete it once the time is up."""

from __future__ import annotations

import asyncio
import logging

import discord

from ..constants import NAME_DEADLINE_MINUTES, UNNAMED_CLOSED_MESSAGE, minutes_to_delta
from ..state import TempVCSession
from ..views.name_prompt import NamePromptView
from .channel_manager import ChannelManager

log = logging.getLogger(__name__)


class NamingService:
    """Posts the naming prompt in each new channel and enforces its deadline.

    A rename from the control panel counts as naming too, because both go through
    ChannelManager.rename, which marks the session as named.
    """

    def __init__(self, channels: ChannelManager) -> None:
        self.channels = channels

    async def ask(self, session: TempVCSession, channel: discord.VoiceChannel) -> None:
        """Ask the owner to name the channel, and start the countdown to deleting it.

        The countdown only starts once the prompt is posted. If the bot can't ask,
        it doesn't delete the channel for going unnamed.
        """
        prompt = NamePromptView(self.channels, session)
        session.name_prompt = prompt
        wait = minutes_to_delta(NAME_DEADLINE_MINUTES)
        await prompt.post(channel, NAME_DEADLINE_MINUTES, discord.utils.utcnow() + wait)
        if session.closed:
            prompt.stop()  # The channel closed while the prompt was being posted.
            return
        session.naming_task = asyncio.create_task(
            self._enforce_deadline(session, prompt, wait.total_seconds()), name=f"naming-{session.channel_id}"
        )

    async def _enforce_deadline(self, session: TempVCSession, prompt: NamePromptView, seconds: float) -> None:
        """Wait for a name. Delete the channel if none arrives in time."""
        try:
            await asyncio.wait_for(session.named.wait(), timeout=seconds)
        except TimeoutError:
            await self.channels.close_session(session, "it wasn't named in time")
            await self.channels.notify_in_hub(session.owner_id, UNNAMED_CLOSED_MESSAGE)
            return
        await prompt.withdraw()
