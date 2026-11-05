"""Turn the things that can go wrong into log entries and friendly messages."""

import logging
import math

import aiohttp
import discord

log = logging.getLogger(__name__)


class ChannelGoneError(discord.DiscordException):
    """The temporary voice channel no longer exists."""


class CategoryMissingError(discord.DiscordException):
    """The category configured for temporary channels can't be found."""


# Everything a call to Discord can raise, for code that must keep running afterwards.
# RateLimited is covered by DiscordException. It is not a kind of HTTPException,
# so `except discord.HTTPException` alone would miss it. TimeoutError is an OSError.
API_ERRORS = (discord.DiscordException, aiohttp.ClientError, OSError)


def describe_wait(seconds: float) -> str:
    """Describe a wait in plain words, such as "about 8 minutes"."""
    minutes = math.ceil(seconds / 60)
    return "about 1 minute" if minutes <= 1 else f"about {minutes} minutes"


def friendly_error(error: BaseException) -> str:
    """Pick a short message for the person affected by an error."""
    if isinstance(error, discord.RateLimited):
        return f"Discord is limiting how fast I can do that. Please try again in {describe_wait(error.retry_after)}."
    if isinstance(error, discord.Forbidden):
        return "I don't have permission to do that. Please ask a server admin to check my role."
    if isinstance(error, ChannelGoneError | discord.NotFound):
        return "This channel no longer exists."
    if isinstance(error, CategoryMissingError):
        return "I can't find the category for temporary channels. Please tell a server admin."
    return "Something went wrong on my side. Please try again in a moment."


async def report_interaction_error(interaction: discord.Interaction, error: Exception) -> None:
    """Log an error from a button or form, and tell the person who used it."""
    if isinstance(error, discord.RateLimited | discord.Forbidden | discord.NotFound | ChannelGoneError):
        log.warning("A control panel action failed: %s", error)
    else:
        log.error("Unexpected error in a control panel action.", exc_info=error)

    message = friendly_error(error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except API_ERRORS as reply_error:
        log.warning("Could not tell the user about the error: %s", reply_error)
