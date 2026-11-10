"""The rename and user limit forms that pop up from the control panel and the naming prompt."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..constants import (
    INVALID_LIMIT_MESSAGE,
    INVALID_NAME_MESSAGE,
    INVALID_STATUS_MESSAGE,
    MAX_CHANNEL_NAME_LENGTH,
    MAX_CHANNEL_STATUS_LENGTH,
    MAX_USER_LIMIT,
    MIN_CHANNEL_NAME_LENGTH,
    MIN_CHANNEL_STATUS_LENGTH,
    MODAL_TIMEOUT_SECONDS,
    NO_USER_LIMIT,
    REJECTED_NAME_MESSAGE,
    REJECTED_STATUS_MESSAGE,
    RENAME_LIMIT_MESSAGE,
    STATUS_FIELD_LABEL,
    STATUS_FIELD_PLACEHOLDER,
    STATUS_PERMISSION_MESSAGE,
)
from ..errors import describe_wait, friendly_error, report_interaction_error

if TYPE_CHECKING:
    from ..services.channel_manager import ChannelManager
    from ..state import TempVCSession
    from .control_panel import ControlPanelView

log = logging.getLogger(__name__)


def parse_user_limit(text: str) -> int | None:
    """Turn what the owner typed into a limit, or None if it isn't valid."""
    cleaned = text.strip()
    if not cleaned.isdecimal():
        return None
    value = int(cleaned)
    return value if NO_USER_LIMIT <= value <= MAX_USER_LIMIT else None


class RenameModal(discord.ui.Modal, title="Rename your channel"):
    """Asks the owner for a channel name and a status. Both are required.

    The control panel and the naming prompt both use it.
    """

    new_name = discord.ui.TextInput(
        label="New channel name",
        required=True,
        min_length=MIN_CHANNEL_NAME_LENGTH,
        max_length=MAX_CHANNEL_NAME_LENGTH,
    )
    new_status = discord.ui.TextInput(
        label=STATUS_FIELD_LABEL,
        placeholder=STATUS_FIELD_PLACEHOLDER,
        required=True,
        min_length=MIN_CHANNEL_STATUS_LENGTH,
        max_length=MAX_CHANNEL_STATUS_LENGTH,
    )

    def __init__(
        self,
        channels: ChannelManager,
        session: TempVCSession,
        *,
        current_name: str | None = None,
        title: str = discord.utils.MISSING,
    ) -> None:
        """With no `current_name`, the name box starts empty, so the owner must type a name.

        The status box starts with the status the owner last set, or empty if there is none.
        """
        super().__init__(title=title, timeout=MODAL_TIMEOUT_SECONDS)
        self.channels = channels
        self.session = session
        self.new_name.default = current_name
        self.new_status.default = session.status

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Set the channel's status if that changed, rename it, and tell the owner how it went."""
        name = self.new_name.value.strip()
        status = self.new_status.value.strip()
        # Discord's form counts spaces as characters, so an empty name or status can get this far.
        if not name or not status:
            message = INVALID_NAME_MESSAGE if not name else INVALID_STATUS_MESSAGE
            await interaction.response.send_message(message, ephemeral=True)
            return
        # Acknowledge first: Discord fails any form not answered within 3 seconds.
        # thinking=True shows the owner a private "thinking…" until the result is ready.
        await interaction.response.defer(thinking=True, ephemeral=True)
        status_reply = None
        # Only when it changed, so leaving the box alone never overwrites a status
        # someone set in Discord itself since.
        if status != self.session.status:
            try:
                status_reply = await self._set_status(status)
            except discord.HTTPException as error:
                if error.status != 400:  # For example, Discord doesn't allow a word in the status.
                    raise  # on_error below handles anything else.
                # Stop before the rename: the status is required, so a refused one
                # mustn't name the channel or use up one of the renames.
                await interaction.followup.send(REJECTED_STATUS_MESSAGE, ephemeral=True)
                return
        replies = [await self._rename(name), status_reply]
        await interaction.followup.send("\n".join(reply for reply in replies if reply), ephemeral=True)

    async def _rename(self, name: str) -> str:
        """Try the rename, and return the message to show the owner."""
        try:
            await self.channels.rename(self.session, name)
        except discord.RateLimited as error:
            return RENAME_LIMIT_MESSAGE.format(wait=describe_wait(error.retry_after))
        except discord.HTTPException as error:
            if error.status == 400:  # For example, Discord doesn't allow a word in the name.
                return REJECTED_NAME_MESSAGE
            raise  # on_error below handles anything else.
        return f"✅ Channel renamed to **{discord.utils.escape_markdown(name)}**."

    async def _set_status(self, status: str) -> str:
        """Try setting the status, and return the message to show the owner.

        A status Discord refuses raises, so on_submit can stop before the rename.
        The bot's own problems, a missing permission or a rate limit, become a
        message instead and the rename goes ahead. They aren't the owner's fault,
        and on a naming deadline they would otherwise get the channel deleted.
        """
        try:
            await self.channels.set_status(self.session, status)
        except discord.Forbidden as error:
            log.warning("Could not set the status of channel %s: %s", self.session.channel_id, error)
            return STATUS_PERMISSION_MESSAGE
        except discord.RateLimited as error:
            return friendly_error(error)
        return f"✅ Status set to **{discord.utils.escape_markdown(status)}**."

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
