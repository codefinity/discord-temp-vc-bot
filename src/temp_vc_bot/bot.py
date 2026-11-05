"""Create the bot and connect its parts together."""

import discord
from discord.ext import commands

from .config import Settings
from .constants import MAX_RATE_LIMIT_WAIT_SECONDS
from .services.channel_manager import ChannelManager
from .services.timer import TimerService
from .state import SessionRegistry


def build_intents() -> discord.Intents:
    """Ask Discord for only the events the bot needs. Neither of these is privileged."""
    intents = discord.Intents.none()
    intents.guilds = True  # The server's channels and roles.
    intents.voice_states = True  # Who joins and leaves voice channels.
    return intents


class TempVCBot(commands.Bot):
    """The bot itself, holding the shared session list and services."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            # The bot has no text commands. discord.py requires a prefix anyway, and
            # this choice needs no message access.
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=build_intents(),
            # Nothing the bot says pings anyone, unless a message explicitly allows it.
            # This stops a channel name like "@everyone" from pinging the whole server.
            allowed_mentions=discord.AllowedMentions.none(),
            # See the note on this constant. Rename limits depend on it.
            max_ratelimit_timeout=MAX_RATE_LIMIT_WAIT_SECONDS,
            # The bot doesn't receive message events, so a message cache would stay empty.
            max_messages=None,
        )
        self.settings = settings
        self.sessions = SessionRegistry()
        self.channel_manager = ChannelManager(self, settings, self.sessions)
        self.timer_service = TimerService(self.channel_manager)

    async def setup_hook(self) -> None:
        """Runs once before the bot connects. Loads the event handlers."""
        await self.load_extension("temp_vc_bot.cogs.voice_events")
