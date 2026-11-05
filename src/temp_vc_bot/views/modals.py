"""The rename and user limit forms that pop up from the control panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..constants import (
    INVALID_LIMIT_MESSAGE,
    INVALID_NAME_MESSAGE,
    MAX_CHANNEL_NAME_LENGTH,
    MAX_USER_LIMIT,
    MIN_CHANNEL_NAME_LENGTH,
    MODAL_TIMEOUT_SECONDS,
    NO_USER_LIMIT,
    REJECTED_NAME_MESSAGE,
    RENAME_LIMIT_MESSAGE,
)
from ..errors import describe_wait, report_interaction_error

if TYPE_CHECKING:
    from .control_panel import ControlPanelView


def parse_user_limit(text: str) -> int | None:
    """Turn what the owner typed into a limit, or None if it isn't valid."""
    cleaned = text.strip()
    if not cleaned.isdecimal():
        return None
    value = int(cleaned)
    return value if NO_USER_LIMIT <= value <= MAX_USER_LIMIT else None


class RenameModal(discord.ui.Modal, title="Rename your channel"):
    """Asks the owner for a new channel name."""

    new_name = discord.ui.TextInput(
        label="New channel name",
        min_length=MIN_CHANNEL_NAME_LENGTH,
        max_length=MAX_CHANNEL_NAME_LENGTH,
    )

    def __init__(self, panel: ControlPanelView, current_name: str) -> None:
        super().__init__(timeout=MODAL_TIMEOUT_SECONDS)
        self.panel = panel
        self.new_name.default = current_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Rename the channel and tell the owner how it went."""
        name = self.new_name.value.strip()
        # Discord's form counts spaces as characters, so an empty name can get this far.
        if not name:
            await interaction.response.send_message(INVALID_NAME_MESSAGE, ephemeral=True)
            return
        # Acknowledge first: Discord fails any form not answered within 3 seconds.
        # thinking=True shows the owner a private "thinking…" until the result is ready.
        await interaction.response.defer(thinking=True, ephemeral=True)
        await interaction.followup.send(await self._rename(name), ephemeral=True)

    async def _rename(self, name: str) -> str:
        """Try the rename, and return the message to show the owner."""
        try:
            await self.panel.channels.rename(self.panel.session, name)
        except discord.RateLimited as error:
            return RENAME_LIMIT_MESSAGE.format(wait=describe_wait(error.retry_after))
        except discord.HTTPException as error:
            if error.status == 400:  # For example, Discord doesn't allow a word in the name.
                return REJECTED_NAME_MESSAGE
            raise  # on_error below handles anything else.
        return f"✅ Channel renamed to **{discord.utils.escape_markdown(name)}**."

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Log an unexpected error and show the owner a short message."""
        await report_interaction_error(interaction, error)


class UserLimitModal(discord.ui.Modal, title="Set a user limit"):
    """Asks the owner how many people may join."""

    limit = discord.ui.TextInput(
        label=f"Max people, {NO_USER_LIMIT}–{MAX_USER_LIMIT} ({NO_USER_LIMIT} = no limit)",
        min_length=1,
        max_length=len(str(MAX_USER_LIMIT)),
    )

    def __init__(self, panel: ControlPanelView) -> None:
        super().__init__(timeout=MODAL_TIMEOUT_SECONDS)
        self.panel = panel
        self.limit.default = str(panel.session.requested_limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Apply the new limit and tell the owner how it went."""
        requested = parse_user_limit(self.limit.value)
        if requested is None:
            await interaction.response.send_message(INVALID_LIMIT_MESSAGE, ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        people_who_fit = await self.panel.channels.set_user_limit(self.panel.session, requested)
        await self.panel.refresh_message()
        await interaction.followup.send(self._result_message(requested, people_who_fit), ephemeral=True)

    @staticmethod
    def _result_message(requested: int, people_who_fit: int) -> str:
        """Describe the result, including when Discord's maximum got in the way."""
        if requested == NO_USER_LIMIT:
            return "✅ User limit removed."
        if people_who_fit < requested:
            return (
                f"✅ User limit set, but only {people_who_fit} people fit: "
                f"Discord's maximum is {MAX_USER_LIMIT} seats, and bots in the channel use some of them."
            )
        return f"✅ User limit set to {requested}."

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Log an unexpected error and show the owner a short message."""
        await report_interaction_error(interaction, error)
