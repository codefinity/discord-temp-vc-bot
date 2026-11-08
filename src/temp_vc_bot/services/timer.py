"""The countdown: when it starts, the extension prompt, and closing the channel."""

from __future__ import annotations

import asyncio
import io
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import discord

from .. import countdown_ring
from ..constants import (
    ANSWER_WINDOW_MINUTES,
    EXTEND_QUESTION,
    EXTENSION_MINUTES,
    RING_REDRAW_MINUTES,
    TIMER_REPOST_DELAY_SECONDS,
    WAITING_MESSAGE,
    minutes_to_delta,
)
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


def _closing_time(moment: datetime | None) -> str:
    """Format a time as <t:UNIX_TIMESTAMP:T>, which Discord shows in each viewer's own time zone.

    This is a fixed time on purpose, not a live countdown (<t:...:R>). A live
    countdown changes every second inside Discord, and that made the ring flicker.
    With a fixed time, nothing in the timer message changes between the bot's own
    edits. The ring shows how long is left.
    """
    return f"at {discord.utils.format_dt(moment, 'T')}" if moment else "soon"


def _timer_text(session: TempVCSession) -> str:
    """The timer embed's wording for the session's current phase."""
    match session.phase:
        case TimerPhase.WAITING:
            return WAITING_MESSAGE
        case TimerPhase.RUNNING:
            return f"This channel closes {_closing_time(session.ends_at)}."
        case TimerPhase.AWAITING_ANSWER:
            deadline = _closing_time(session.answer_deadline)
            return f"Time's up! This channel closes {deadline} unless the owner extends it."
        case TimerPhase.EXTENDED:
            return (
                f"Extended by {EXTENSION_MINUTES} minutes. "
                f"This channel closes {_closing_time(session.ends_at)} and can't be extended again."
            )
        case TimerPhase.ENDING:
            return "Time's up. This channel is closing now."


def build_timer_embed(session: TempVCSession, *, with_ring: bool = True) -> discord.Embed:
    """Build the timer embed from the session's current state.

    With `with_ring`, the embed shows the attached ring image under the text.
    """
    embed = discord.Embed(title="⏳ Timer", description=_timer_text(session), colour=_PHASE_COLOURS[session.phase])
    if with_ring:
        embed.set_image(url=f"attachment://{countdown_ring.FILENAME}")
    return embed


def ring_face(session: TempVCSession, now: datetime) -> tuple[float, str, str]:
    """What the ring shows at `now`: how full it is, the big text, and the small text.

    The ring counts down whichever stretch of time the session is in: the base
    time, the owner's answer window, or the extra time. The big number is the
    minutes left, rounded up, so it reaches 0 only when time is up.
    """
    match session.phase:
        case TimerPhase.WAITING:
            return 1.0, "Waiting", "for someone to join"
        case TimerPhase.RUNNING:
            deadline, length, caption = session.ends_at, session.timer_minutes, "min left"
        case TimerPhase.AWAITING_ANSWER:
            deadline, length, caption = session.answer_deadline, ANSWER_WINDOW_MINUTES, "min to answer"
        case TimerPhase.EXTENDED:
            deadline, length, caption = session.ends_at, EXTENSION_MINUTES, "min left"
        case TimerPhase.ENDING:
            return 0.0, "0", "closing"
    assert deadline is not None, "A running timer always has a deadline."
    minutes_left = max((deadline - now) / minutes_to_delta(1), 0.0)
    return minutes_left / length, str(math.ceil(minutes_left)), caption


def redraw_times(deadline: datetime, now: datetime) -> list[datetime]:
    """When to redraw the ring between `now` and `deadline`.

    The times are counted back from the deadline in steps of RING_REDRAW_MINUTES,
    rather than forward from now. That way each redraw lands exactly on a step,
    so the minutes shown are exact at the moment the ring is drawn. The deadline
    itself isn't included, because the phase change at that moment redraws anyway.
    """
    step = minutes_to_delta(RING_REDRAW_MINUTES)
    steps_before_deadline = math.ceil((deadline - now) / step) - 1
    return [deadline - count * step for count in range(steps_before_deadline, 0, -1)]


async def draw_ring(session: TempVCSession, as_of: datetime) -> discord.File | None:
    """Draw the ring for the session's state at `as_of`, as a file to attach.

    Returns None if drawing fails, so the timer carries on with text only. A
    broken image must never stop a channel from closing on time.
    """
    colour = _PHASE_COLOURS[session.phase].to_rgb()
    try:
        fraction_left, headline, caption = ring_face(session, as_of)
        # Drawing takes tens of milliseconds, so it runs in a thread to keep the
        # bot responding to everything else meanwhile.
        png = await asyncio.to_thread(countdown_ring.render_ring, fraction_left, colour, headline, caption)
    except Exception:
        log.exception("Could not draw the countdown ring for channel %s.", session.channel_id)
        return None
    return discord.File(io.BytesIO(png), filename=countdown_ring.FILENAME)


class TimerService:
    """Runs one countdown per temp VC."""

    def __init__(self, channels: ChannelManager) -> None:
        self.channels = channels

    async def post_timer_message(self, session: TempVCSession, channel: discord.VoiceChannel) -> None:
        """Post the timer embed and its ring at the bottom of the chat."""
        ring = await draw_ring(session, discord.utils.utcnow())
        embed = build_timer_embed(session, with_ring=ring is not None)
        message = await channel.send(embed=embed, files=[ring] if ring else [])
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

    async def _update_timer_message(self, session: TempVCSession, *, as_of: datetime | None = None) -> None:
        """Redraw the timer embed and its ring, showing the time left at `as_of` (default: now).

        This runs when the phase changes and every RING_REDRAW_MINUTES in between.
        The text only changes with the phase, because it shows a fixed closing time.
        """
        # One edit at a time, each drawn from the state at the moment it is sent.
        # Otherwise a slow redraw could land after a newer phase change and undo it.
        async with session.timer_message_lock:
            channel = self.channels.get_channel(session)
            if channel is None or session.timer_message_id is None:
                return
            ring = await draw_ring(session, as_of or discord.utils.utcnow())
            embed = build_timer_embed(session, with_ring=ring is not None)
            try:
                message = channel.get_partial_message(session.timer_message_id)
                # Listing only the new ring replaces the previous one.
                await message.edit(embed=embed, attachments=[ring] if ring else [])
            except API_ERRORS as error:
                log.warning("Could not update the timer message in channel %s: %s", session.channel_id, error)

    # ----- Keeping the timer in view ----------------------------------------
    # Discord has no way to keep a message on screen. A pinned message only shows
    # in the pins list, so instead the bot posts the timer again below the chat.

    def note_new_message(self, session: TempVCSession, message_id: int) -> None:
        """Note that something was posted in the channel, which may have pushed the timer up.

        After TIMER_REPOST_DELAY_SECONDS the timer is posted again at the bottom.
        """
        if session.closed or session.timer_message_id is None or message_id == session.timer_message_id:
            return
        session.timer_buried = True
        if session.repost_task is None or session.repost_task.done():
            session.repost_task = asyncio.create_task(
                self._keep_timer_at_bottom(session), name=f"repost-{session.channel_id}"
            )

    async def _keep_timer_at_bottom(self, session: TempVCSession) -> None:
        """Repost the timer after a pause, and again while people keep posting below it."""
        while session.timer_buried:
            await asyncio.sleep(TIMER_REPOST_DELAY_SECONDS)
            # Cleared before reposting, so a message posted during the repost
            # brings another round.
            session.timer_buried = False
            await self._repost_timer_message(session)

    async def _repost_timer_message(self, session: TempVCSession) -> None:
        """Post the timer again at the bottom of the chat, then delete the old one.

        The new one goes up first, so the timer never disappears. If posting
        fails, the old one stays and keeps being updated.
        """
        async with session.timer_message_lock:
            channel = self.channels.get_channel(session)
            old_id = session.timer_message_id
            if session.closed or channel is None or old_id is None:
                return
            # Nothing has been posted below it since. This happens when Discord
            # reports the bot's own repost before replying to the post itself, so
            # the repost looked like someone else's message for a moment.
            if channel.last_message_id == old_id:
                return
            try:
                await self.post_timer_message(session, channel)
            except API_ERRORS as error:
                log.warning("Could not move the timer down in channel %s: %s", session.channel_id, error)
                return
            try:
                await channel.get_partial_message(old_id).delete()
            except API_ERRORS as error:
                log.warning("Could not delete the old timer message in channel %s: %s", session.channel_id, error)

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

    async def _sleep_until_end(self, session: TempVCSession) -> None:
        """Wait until the session's current end time, redrawing the ring on the way."""
        assert session.ends_at is not None, "The timer must have started before it can run out."
        async with self._redrawing(session, session.ends_at):
            await discord.utils.sleep_until(session.ends_at)

    @asynccontextmanager
    async def _redrawing(self, session: TempVCSession, deadline: datetime) -> AsyncIterator[None]:
        """Redraw the ring on schedule until `deadline`, while the `async with` block runs.

        The redraws run as their own task, so a slow edit can never hold up the
        countdown itself. Leaving the block stops them.
        """
        redraws = asyncio.create_task(self._redraw_until(session, deadline), name=f"ring-{session.channel_id}")
        try:
            yield
        finally:
            redraws.cancel()

    async def _redraw_until(self, session: TempVCSession, deadline: datetime) -> None:
        """Redraw the ring at each of the redraw times before `deadline`."""
        for moment in redraw_times(deadline, discord.utils.utcnow()):
            await discord.utils.sleep_until(moment)
            # Draw it as of the planned moment, not the clock. asyncio can end a sleep
            # a few milliseconds early, and 14:00.003 left would round up to "15".
            await self._update_timer_message(session, as_of=moment)

    async def _owner_wants_extension(self, session: TempVCSession) -> bool:
        """Ask the owner whether to extend. No answer in time counts as No."""
        channel = self.channels.get_channel(session)
        if channel is None:
            return False

        deadline = discord.utils.utcnow() + minutes_to_delta(ANSWER_WINDOW_MINUTES)
        session.await_answer(deadline)
        await self._update_timer_message(session)

        view = ExtendPromptView(session.owner_id)
        try:
            await channel.send(
                f"<@{session.owner_id}> {EXTEND_QUESTION}",
                view=view,
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(session.owner_id)]),
            )
            async with self._redrawing(session, deadline):
                await view.wait()
        except API_ERRORS as error:
            log.warning("Could not ask the owner of channel %s about extending: %s", session.channel_id, error)
        finally:
            view.stop()
        return view.wants_extension
