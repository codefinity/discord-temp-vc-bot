"""A base for views that only the channel owner may use."""

import discord

from ..constants import NOT_OWNER_MESSAGE
from ..errors import report_interaction_error


class OwnerOnlyView(discord.ui.View):
    """Buttons and dropdowns that answer everyone but the owner with a private note."""

    def __init__(self, owner_id: int, *, timeout: float | None) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Runs before every click. Returning False stops the click there.

        The check has to live here and not in each button: discord.py restarts a
        view's timeout on any click that passes this check. Rejected clicks don't
        restart it, so nobody else can keep the extension prompt open by clicking it.
        """
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(NOT_OWNER_MESSAGE, ephemeral=True)
        return False

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """Log any error from a button and show the owner a short message."""
        await report_interaction_error(interaction, error)
