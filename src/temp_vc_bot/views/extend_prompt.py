"""The Yes and No buttons shown when a channel's time runs out."""

import discord

from ..constants import ANSWER_WINDOW_MINUTES, EXTENSION_MINUTES, NO_LABEL, YES_LABEL, minutes_to_delta
from .owner_only import OwnerOnlyView


class ExtendPromptView(OwnerOnlyView):
    """Asks the owner one question and stops when answered or when time runs out."""

    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=minutes_to_delta(ANSWER_WINDOW_MINUTES).total_seconds())
        # Stays False if the owner says No or never answers. Both mean "close it".
        self.wants_extension = False

    @discord.ui.button(label=YES_LABEL, style=discord.ButtonStyle.success)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Keep the channel open for the extra time."""
        self.wants_extension = True
        await self._finish(interaction, f"✅ Extended by {EXTENSION_MINUTES} minutes. This can't be extended again.")

    @discord.ui.button(label=NO_LABEL, style=discord.ButtonStyle.danger)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Close the channel."""
        await self._finish(interaction, "👋 Closing this channel.")

    async def _finish(self, interaction: discord.Interaction, text: str) -> None:
        """Replace the question with the result, then let the timer carry on."""
        try:
            await interaction.response.edit_message(content=text, view=None)
        finally:
            # This wakes the timer, even if the edit above failed. Replying first
            # matters for No, because waking the timer deletes the channel.
            self.stop()
