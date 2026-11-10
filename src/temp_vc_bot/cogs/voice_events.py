"""React to people joining and leaving voice channels, and to chat in temp VCs."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..errors import API_ERRORS, friendly_error
from ..services.channel_manager import human_members
from ..state import TempVCSession
from ..views.control_panel import ControlPanelView

if TYPE_CHECKING:
    from ..bot import TempVCBot

log = logging.getLogger(__name__)


def _channel_id(voice_state: discord.VoiceState) -> int | None:
    """The ID of the channel in a voice state, or None if the member isn't in one."""
    return voice_state.channel.id if voice_state.channel else None


class VoiceEvents(commands.Cog):
    """Turns Discord events into actions on temp VCs."""

    def __init__(self, bot: TempVCBot) -> None:
        self.bot = bot
        self.settings = bot.settings
        self.sessions = bot.sessions
        self.channels = bot.channel_manager
        self.timers = bot.timer_service
        self.naming = bot.naming_service
        # Handles hub joins one at a time. Otherwise someone double-clicking the hub
        # could get two channels, because creating a channel takes a moment.
        self._hub_lock = asyncio.Lock()
        self._leftovers_deleted = False

    # ----- Startup ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Get things in order once connected. This can run more than once."""
        log.info("Logged in as %s.", self.bot.user)
        self.channels.report_setup_problems()

        # Discord can fire on_ready again after a long disconnect. Leftovers must only
        # be deleted on the first run, and active channels are never deleted anyway.
        if not self._leftovers_deleted:
            self._leftovers_deleted = True
            await self.channels.delete_leftovers()

        await self._catch_up_on_sessions()
        await self._pick_up_members_waiting_in_hub()

    async def _catch_up_on_sessions(self) -> None:
        """Handle anything that changed while the bot was disconnected."""
        for session in self.sessions:
            if not session.setup_finished:
                continue  # Still being created by _handle_hub_join, which is handling it.
            await self._close_if_empty(session)
            await self.channels.refresh_user_limit(session)
            await self.timers.maybe_start(session)

    async def _pick_up_members_waiting_in_hub(self) -> None:
        """Serve anyone who joined the hub while the bot was offline.

        Discord sends no event for people already in the channel, so without this
        they would sit in the hub until they left and rejoined.
        """
        guild = self.channels.guild
        hub = guild.get_channel(self.settings.hub_channel_id) if guild else None
        if isinstance(hub, discord.VoiceChannel):
            for member in human_members(hub):
                await self._handle_hub_join(member)

    # ----- Voice events -----------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        """Runs whenever anyone's voice state changes."""
        if member.guild.id != self.settings.guild_id:
            return
        before_id, after_id = _channel_id(before), _channel_id(after)
        if before_id == after_id:
            return  # Only something like mute or deafen changed.

        if member.bot:
            await self._on_bot_moved(before_id, after_id)
        else:
            await self._on_human_moved(member, before_id, after_id)

    async def _on_bot_moved(self, before_id: int | None, after_id: int | None) -> None:
        """A bot joined or left a channel, so its seat has to be added or removed."""
        for channel_id in (before_id, after_id):
            session = self.sessions.by_channel(channel_id)
            if session is not None:
                await self.channels.refresh_user_limit(session)

    async def _on_human_moved(self, member: discord.Member, before_id: int | None, after_id: int | None) -> None:
        """A person changed channels: check the channel they left and the one they joined."""
        left = self.sessions.by_channel(before_id)
        joined = self.sessions.by_channel(after_id)
        joined_hub = after_id == self.settings.hub_channel_id

        # An owner who clicks the hub from their own channel gets sent straight back,
        # so their channel must not be deleted for looking empty for that moment.
        owner_coming_back = joined_hub and left is not None and left.owner_id == member.id
        if left is not None and not owner_coming_back:
            await self._close_if_empty(left)

        if joined_hub:
            await self._handle_hub_join(member)
        elif joined is not None:
            await self.timers.maybe_start(joined)

    async def _close_if_empty(self, session: TempVCSession) -> None:
        """Delete a temp VC with no humans left in it. Bots don't count."""
        channel = self.channels.get_channel(session)
        if channel is None:
            await self.channels.close_session(session, "the channel is gone")
        elif not human_members(channel):
            await self.channels.close_session(session, "everyone left")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Forget a temp VC that someone deleted by hand, and cancel its timer."""
        session = self.sessions.by_channel(channel.id)
        if session is not None:
            await self.channels.close_session(session, "the channel was deleted by someone else")

    # ----- Chat -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Keep the timer at the bottom of a temp VC's chat as messages arrive."""
        session = self.sessions.by_channel(message.channel.id)
        if session is not None:
            self.timers.note_new_message(session, message.id)

    # ----- The hub ----------------------------------------------------------

    async def _handle_hub_join(self, member: discord.Member) -> None:
        """Send a member to their existing temp VC, or create one for them."""
        async with self._hub_lock:
            # They may have left while waiting for their turn.
            if not self._is_in_hub(member):
                return
            existing = self.sessions.by_owner(member.id)
            if existing is not None:
                await self._send_back(member, existing)
                return
            session = await self._create_and_move(member)

        # This is outside the lock so that one slow setup can't make everybody else
        # wait for their channel.
        if session is not None:
            await self._finish_setup(member, session)

    def _is_in_hub(self, member: discord.Member) -> bool:
        """Whether a member is in the hub right now."""
        return member.voice is not None and _channel_id(member.voice) == self.settings.hub_channel_id

    async def _send_back(self, member: discord.Member, session: TempVCSession) -> None:
        """Move an owner back into the temp VC they already have."""
        if not await self.channels.move_member(member, session):
            # The owner may have been the last person in there. No "leave" event is
            # coming to trigger the cleanup, so check now.
            await self._close_if_empty(session)

    async def _create_and_move(self, member: discord.Member) -> TempVCSession | None:
        """Create a temp VC and move its owner in. Returns None if that failed."""
        try:
            session = await self.channels.create_session(member)
        except API_ERRORS as error:
            log.error("Could not create a temp VC for member %s: %s", member.id, error)
            await self.channels.notify_in_hub(member.id, friendly_error(error))
            return None

        if await self.channels.move_member(member, session):
            return session

        # Nobody ever entered this channel, so no "leave" event would ever clean it up.
        await self.channels.close_session(session, "the owner could not be moved in")
        return None

    async def _finish_setup(self, owner: discord.Member, session: TempVCSession) -> None:
        """Post the controls, then ask the owner to name the channel, then post the timer.

        The naming prompt sits just above the timer. The timer is still the last
        message, so it doesn't have to move below the prompt. The bot never joins the
        voice channel itself. It manages the channel through its permissions, so it
        doesn't show up in the channel or take a seat.
        """
        channel = self.channels.get_channel(session)
        if channel is None or session.closed:
            return

        panel = ControlPanelView(self.channels, session)
        session.panel_view = panel
        try:
            await panel.post(channel)
            await self.naming.ask(session, channel)
            await self.timers.post_timer_message(session, channel)
        except API_ERRORS as error:
            if session.closed:
                return  # The owner left straight away, so there is nothing to report.
            log.error("Could not post the controls in channel %s: %s", channel.id, error)
            await self.channels.notify_in_hub(owner.id, friendly_error(error))
        session.setup_finished = True

        # Someone may have joined while those messages were being sent.
        if session.timer_started:
            await self.timers.publish_state(session)
        else:
            await self.timers.maybe_start(session)


async def setup(bot: TempVCBot) -> None:
    """Called by discord.py when this file is loaded as an extension."""
    await bot.add_cog(VoiceEvents(bot))
