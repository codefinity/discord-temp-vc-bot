"""The /timer command, which shows a countdown in a temp VC's chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..constants import (
    MAX_USER_TIMER_MINUTES,
    MIN_USER_TIMER_MINUTES,
    TIMER_COMMAND_DESCRIPTION,
    TIMER_EXISTS_MESSAGE,
    TIMER_JOIN_VOICE_MESSAGE,
    TIMER_MINUTES_DESCRIPTION,
    TIMER_NOT_TEMP_VC_MESSAGE,
    TIMER_POSTED_MESSAGE,
)
from ..errors import API_ERRORS, report_interaction_error

if TYPE_CHECKING:
    from ..bot import TempVCBot

Minutes = app_commands.Range[int, MIN_USER_TIMER_MINUTES, MAX_USER_TIMER_MINUTES]


class UserTimers(commands.Cog):
    """Holds the /timer command, and stops a timer whose message is deleted."""

    def __init__(self, bot: TempVCBot) -> None:
        self.sessions = bot.sessions
        self.channels = bot.channel_manager
        self.timers = bot.user_timer_service

    @app_commands.command(name="timer", description=TIMER_COMMAND_DESCRIPTION)
    @app_commands.describe(minutes=TIMER_MINUTES_DESCRIPTION)
    @app_commands.guild_only()
    async def timer(self, interaction: discord.Interaction, minutes: Minutes) -> None:
        """Post a timer in this temp VC's chat, for the people in the channel."""
        session = self.sessions.by_channel(interaction.channel_id)
        channel = self.channels.get_channel(session) if session is not None else None
        # Only temp VCs, where the bot is sure to have the permissions it needs, and
        # where the timer goes when the channel closes.
        if session is None or channel is None:
            await interaction.response.send_message(TIMER_NOT_TEMP_VC_MESSAGE, ephemeral=True)
            return
        if interaction.user.id not in channel.voice_states:
            await interaction.response.send_message(TIMER_JOIN_VOICE_MESSAGE, ephemeral=True)
            return
        if session.user_timer is not None:
            await interaction.response.send_message(TIMER_EXISTS_MESSAGE, ephemeral=True)
            return

        timer = self.timers.create(session, interaction.user.id, minutes)
        try:
            # Answered first, because Discord fails a command not answered within 3 seconds.
            await interaction.response.send_message(TIMER_POSTED_MESSAGE, ephemeral=True)
            await self.timers.post(timer)
        except API_ERRORS as error:
            self.timers.discard(timer)  # So the channel can have another go.
            await report_interaction_error(interaction, error)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """Stop a timer whose message someone deleted, so the channel can have a new one."""
        self._discard_if_deleted(payload.channel_id, {payload.message_id})

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        """The same, for a moderator clearing many messages at once."""
        self._discard_if_deleted(payload.channel_id, payload.message_ids)

    def _discard_if_deleted(self, channel_id: int, deleted_ids: set[int]) -> None:
        session = self.sessions.by_channel(channel_id)
        timer = session.user_timer if session is not None else None
        if timer is not None and timer.message_id in deleted_ids:
            self.timers.discard(timer)


async def setup(bot: TempVCBot) -> None:
    """Called by discord.py when this file is loaded as an extension.

    Like /topic, the command is registered only to the one server the bot serves.
    """
    await bot.add_cog(UserTimers(bot), guild=discord.Object(bot.settings.guild_id))
