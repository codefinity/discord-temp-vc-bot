"""The countdown: when it starts, the extension prompt, and closing the channel."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord

from ..constants import ANSWER_WINDOW_MINUTES, EXTEND_QUESTION, EXTENSION_MINUTES, WAITING_MESSAGE, minutes_to_delta
from ..errors import API_ERRORS
from ..state import TempVCSession, TimerPhase
from ..views.extend_prompt import ExtendPromptView
from .channel_manager import ChannelManager, has_other_human

log = logging.getLogger(__name__)

_PHASE_COLOURS = {
    TimerPhase.WAITING: discord.Colour.light_grey(),
    TimerPhase.RUNNING: discord.Colour.green(),
    TimerPhase.AWAITING_ANSWER: discord.Colour.orange(),
    TimerPhase.EXTENDED: discord.Colour.blue(),
    TimerPhase.ENDING: discord.Colour.red(),
}


def _live_countdown(moment: datetime | None) -> str:
    """Format a time as <t:UNIX_TIMESTAMP:R>, which every Discord client counts down by itself.

    This is why the bot only has to edit the message when the phase changes.
    """
    return discord.utils.format_dt(moment, "R") if moment else "soon"


def _timer_text(session: TempVCSession) -> str:
    """The timer embed's wording for the session's current phase."""
    match session.phase:
        case TimerPhase.WAITING:
            return WAITING_MESSAGE
        case TimerPhase.RUNNING:
            return f"This channel closes {_live_countdown(session.ends_at)}."
        case TimerPhase.AWAITING_ANSWER:
            deadline = _live_countdown(session.answer_deadline)
            return f"Time's up! This channel closes {deadline} unless the owner extends it."
        case TimerPhase.EXTENDED:
            return (
                f"Extended by {EXTENSION_MINUTES} minutes. "
                f"This channel closes {_live_countdown(session.ends_at)} and can't be extended again."
            )
        case TimerPhase.ENDING:
            return "Time's up. This channel is closing now."


def build_timer_embed(session: TempVCSession) -> discord.Embed:
    """Build the timer embed from the session's current state."""
    return discord.Embed(title="⏳ Timer", description=_timer_text(session), colour=_PHASE_COLOURS[session.phase])


class TimerService:
    """Runs one countdown per temp VC."""

    def __init__(self, channels: ChannelManager) -> None:
        self.channels = channels

    async def post_timer_message(self, session: TempVCSession, channel: discord.VoiceChannel) -> None:
        """Post the timer embed for the first time."""
        message = await channel.send(embed=build_timer_embed(session))
        session.timer_message_id = message.id

    async def maybe_start(self, session: TempVCSession) -> None:
        """Start the countdown if a human other than the owner is in the channel."""
        if session.closed or session.timer_started:
            return
        channel = self.channels.get_channel(session)
        if channel is None or not has_other_human(channel, session.owner_id):
            return

        # Nothing is awaited between the check above and this line, so two people
        # joining at the same moment can't start two countdowns.
        session.start_timer(discord.utils.utcnow())
        session.timer_task = asyncio.create_task(self._run(session), name=f"timer-{session.channel_id}")
        log.info("Started the %d-minute timer for channel %s.", session.timer_minutes, session.channel_id)
        await self.publish_state(session)

    async def publish_state(self, session: TempVCSession) -> None:
        """Show the current state in the timer embed and on the control panel."""
        await self._update_timer_message(session)
        if session.panel_view is not None:
            await session.panel_view.refresh_message()

    async def _update_timer_message(self, session: TempVCSession) -> None:
        """Edit the timer embed. It is only called when the phase changes, to avoid rate limits."""
        channel = self.channels.get_channel(session)
        if channel is None or session.timer_message_id is None:
            return
        try:
            message = channel.get_partial_message(session.timer_message_id)
            await message.edit(embed=build_timer_embed(session))
        except API_ERRORS as error:
            log.warning("Could not update the timer message in channel %s: %s", session.channel_id, error)

    async def _run(self, session: TempVCSession) -> None:
        """Count down, then close the channel. Runs as a background task."""
        try:
            await self._count_down(session)
        except Exception:
            # Fall through and close the channel, so an unexpected error can't leave
            # it open forever. Cancelling the task is not an Exception, so it skips this.
            log.exception("The timer for channel %s failed. Closing the channel.", session.channel_id)

        session.phase = TimerPhase.ENDING
        await self._update_timer_message(session)
        await self.channels.close_session(session, "the timer ended")

    async def _count_down(self, session: TempVCSession) -> None:
        """Wait out the base time, ask about extending, and wait out any extra time."""
        await self._sleep_until_end(session)
        if await self._owner_wants_extension(session):
            session.extend()
            log.info("The owner extended channel %s.", session.channel_id)
            await self._update_timer_message(session)
            await self._sleep_until_end(session)
            # No second question here: when the extra time ends, the channel closes.

    @staticmethod
    async def _sleep_until_end(session: TempVCSession) -> None:
        """Wait until the session's current end time."""
        assert session.ends_at is not None, "The timer must have started before it can run out."
        await discord.utils.sleep_until(session.ends_at)

    async def _owner_wants_extension(self, session: TempVCSession) -> bool:
        """Ask the owner whether to extend. No answer in time counts as No."""
        channel = self.channels.get_channel(session)
        if channel is None:
            return False

        session.await_answer(discord.utils.utcnow() + minutes_to_delta(ANSWER_WINDOW_MINUTES))
        await self._update_timer_message(session)

        view = ExtendPromptView(session.owner_id)
        try:
            await channel.send(
                f"<@{session.owner_id}> {EXTEND_QUESTION}",
                view=view,
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(session.owner_id)]),
            )
            await view.wait()
        except API_ERRORS as error:
            log.warning("Could not ask the owner of channel %s about extending: %s", session.channel_id, error)
        finally:
            view.stop()
        return view.wants_extension
