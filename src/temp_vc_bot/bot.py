"""Create the bot and connect its parts together."""

import logging

import discord
from discord.ext import commands

from . import ratelimit_fix
from .config import Settings
from .constants import MAX_RATE_LIMIT_WAIT_SECONDS
from .errors import API_ERRORS
from .services.channel_manager import ChannelManager
from .services.chat_reminder import ChatReminder
from .services.naming import NamingService
from .services.session_setup import SessionSetup
from .services.timer import TimerService
from .services.topics import TopicService
from .state import SessionRegistry

log = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    """Ask Discord for only the events the bot needs. None of these is privileged."""
    intents = discord.Intents.none()
    intents.guilds = True  # The server's channels and roles.
    intents.voice_states = True  # Who joins and leaves voice channels.
    # That a message was posted, so the timer can move back below it. The bot never
    # reads what people write, so it doesn't need the privileged Message Content intent.
    intents.guild_messages = True
    return intents


class TempVCBot(commands.Bot):
    """The bot itself, holding the shared session list and services."""

    def __init__(self, settings: Settings) -> None:
        # Without this, max_ratelimit_timeout below allows only one new channel per day.
        ratelimit_fix.apply()
        super().__init__(
            # The bot has no text commands, only the /topic slash command. discord.py
            # requires a prefix anyway, and this choice needs no message access.
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=build_intents(),
            # Nothing the bot says pings anyone, unless a message explicitly allows it.
            # This stops a channel name like "@everyone" from pinging the whole server.
            allowed_mentions=discord.AllowedMentions.none(),
            # See the note on this constant. Rename limits depend on it.
            max_ratelimit_timeout=MAX_RATE_LIMIT_WAIT_SECONDS,
            # The bot only needs to know that a message was posted, never to look one
            # up later, so it keeps no message cache.
            max_messages=None,
        )
        self.settings = settings
        self.sessions = SessionRegistry()
        self.channel_manager = ChannelManager(self, settings, self.sessions)
        self.timer_service = TimerService(self.channel_manager)
        self.naming_service = NamingService(self.channel_manager)
        self.session_setup = SessionSetup(self.channel_manager, self.timer_service, self.naming_service)
        self.topic_service = TopicService(self.channel_manager, self.session_setup)
        self.chat_reminder = ChatReminder()

    async def setup_hook(self) -> None:
        """Runs once before the bot connects. Loads the event handlers and /topic."""
        await self.load_extension("temp_vc_bot.cogs.voice_events")
        await self.load_extension("temp_vc_bot.cogs.topics")
        await self._register_commands()

    async def _register_commands(self) -> None:
        """Send Discord the current list of slash commands, so /topic appears in the server.

        Sending the same list again changes nothing, so this is safe on every start.
        """
        try:
            commands_sent = await self.tree.sync(guild=discord.Object(self.settings.guild_id))
        except API_ERRORS as error:
            # The bot still runs. Only /topic is missing until this works.
            log.error(
                "Could not register /topic with Discord: %s. Open the invite link in DEPLOYMENT.md "
                "again so the bot can add commands, and check GUILD_ID.",
                error,
            )
            return
        log.info("Registered %s in the server.", ", ".join(f"/{command.name}" for command in commands_sent))
        # Replies show /topic as a mention, which people can click to fill in the command.
        topic_command = next((command for command in commands_sent if command.name == "topic"), None)
        if topic_command is not None:
            self.chat_reminder.command_mention = topic_command.mention

    async def on_message(self, message: discord.Message, /) -> None:
        """Skip discord.py's search for a text command in every message.

        The bot has none, so the search would only log an error each time someone
        @mentions it. The cog still hears about every message.
        """
