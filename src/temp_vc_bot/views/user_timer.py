"""The Start, Pause and Stop buttons under a timer set with /timer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..constants import PAUSE_LABEL, START_LABEL, STOP_LABEL, TIMER_JOIN_VOICE_MESSAGE
from ..errors import report_interaction_error
from ..state import UserTimer, UserTimerPhase

if TYPE_CHECKING:
    from ..services.user_timer import UserTimerService


class UserTimerView(discord.ui.View):
    """The timer's buttons, for anyone in the voice channel, not only its owner."""

    def __init__(self, timers: UserTimerService, timer: UserTimer) -> None:
        # timeout=None keeps the buttons working for as long as the timer runs.
        super().__init__(timeout=None)
        self.timers = timers
        self.timer = timer
        self.sync()

    def sync(self) -> None:
        """Enable only the buttons that do something in the timer's current phase. Stop always does."""
        phase = self.timer.phase
        self.start_button.disabled = phase not in (UserTimerPhase.READY, UserTimerPhase.PAUSED)
        self.pause_button.disabled = phase not in (UserTimerPhase.COUNTING_IN, UserTimerPhase.RUNNING)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Runs before every click. Only people in the voice channel can use its timer."""
        channel = self.timers.channels.voice_channel(self.timer.channel_id)
        if channel is not None and interaction.user.id in channel.voice_states:
            return True
        await interaction.response.send_message(TIMER_JOIN_VOICE_MESSAGE, ephemeral=True)
        return False

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """Log any error from a button and show the person a short message."""
        await report_interaction_error(interaction, error)

    @discord.ui.button(label=START_LABEL, emoji="▶️", style=discord.ButtonStyle.success)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Count in, then start, or carry on after a pause. The service answers the click."""
        await self.timers.start(self.timer, interaction)

    # Pause and Stop acknowledge the click first, because Discord fails any click not
    # answered within 3 seconds. The timer's message is then updated like any other change.

    @discord.ui.button(label=PAUSE_LABEL, emoji="⏸️", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Hold the time left."""
        await interaction.response.defer()
        await self.timers.pause(self.timer)

    @discord.ui.button(label=STOP_LABEL, emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """End the timer without a ring. Unlike ending a channel, this needs no confirming."""
        await interaction.response.defer()
        await self.timers.stop(self.timer)
