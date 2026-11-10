"""Timers people set with /timer: the count-in, the countdown and the ring.

These are only for the people in a channel. services/timer.py runs the channel's
own timer, which decides when the channel closes, and this file never changes it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import discord

from .. import countdown_ring, sounds
from ..constants import (
    COUNT_IN_BEAT_SECONDS,
    COUNT_IN_FROM,
    RING_SECONDS,
    SOUND_JOIN_SECONDS,
    TIMER_FOOTER,
    USER_TIMER_REDRAW_SECONDS,
    minutes_to_delta,
)
from ..errors import API_ERRORS, ChannelGoneError
from ..state import TempVCSession, UserTimer, UserTimerPhase
from ..views.user_timer import UserTimerView
from .channel_manager import ChannelManager
from .speaker import Speaker
from .timer import redraw_times

log = logging.getLogger(__name__)

# Different from the channel timer's colours, so the two rings aren't confused.
_PHASE_COLOURS = {
    UserTimerPhase.READY: discord.Colour.blurple(),
    UserTimerPhase.COUNTING_IN: discord.Colour.gold(),
    UserTimerPhase.RUNNING: discord.Colour.blurple(),
    UserTimerPhase.PAUSED: discord.Colour.light_grey(),
    UserTimerPhase.FINISHED: discord.Colour.red(),
    UserTimerPhase.STOPPED: discord.Colour.dark_grey(),
}


def clock(left: timedelta) -> str:
    """Show the time left as minutes and seconds, such as 4:30, rounded up to the second.

    It counts timer seconds, which SECONDS_PER_MINUTE speeds up for testing, so
    the numbers stay the normal ones.
    """
    # Rounded first, so that a hair over a whole second isn't rounded up to the next one.
    seconds = math.ceil(round(left / minutes_to_delta(1) * 60, 6))
    return f"{seconds // 60}:{seconds % 60:02d}"


def user_timer_face(timer: UserTimer, now: datetime) -> tuple[float, str, str]:
    """What the ring shows at `now`: how full it is, the big text, and the small text."""
    left = timer.time_left(now)
    fraction_left = left / timer.length
    match timer.phase:
        case UserTimerPhase.COUNTING_IN:
            # The time left until the bot is ready to beep, then 3, 2, 1.
            return fraction_left, str(timer.count) if timer.count else clock(left), "get ready"
        case UserTimerPhase.READY:
            caption = "ready"
        case UserTimerPhase.RUNNING:
            caption = "left"
        case UserTimerPhase.PAUSED:
            caption = "paused"
        case UserTimerPhase.FINISHED:
            caption = "time's up"
        case UserTimerPhase.STOPPED:
            caption = "stopped"
    return fraction_left, clock(left), caption


def _timer_text(timer: UserTimer) -> str:
    """The embed's wording for the timer's current phase."""
    match timer.phase:
        case UserTimerPhase.READY:
            text = (
                f"Click **Start** when everyone's ready. It counts down from {COUNT_IN_FROM} first, "
                "with a beep on each number."
            )
        case UserTimerPhase.COUNTING_IN:
            text = "Starting…"
        case UserTimerPhase.RUNNING:
            # A fixed time, not a live countdown, for the same reason as the channel's timer:
            # a live countdown makes the ring flicker. The ring shows how long is left.
            assert timer.ends_at is not None, "A running timer always has an end time."
            text = f"Ends at {discord.utils.format_dt(timer.ends_at, 'T')}."
        case UserTimerPhase.PAUSED:
            text = "Paused. Click **Start** to carry on."
        case UserTimerPhase.FINISHED:
            text = "⏰ Time's up!"
        case UserTimerPhase.STOPPED:
            text = "Stopped."
    return f"{text}\nSet by <@{timer.set_by}>"


def build_user_timer_embed(timer: UserTimer, *, with_ring: bool = True) -> discord.Embed:
    """Build the timer's embed. With `with_ring`, it shows the attached ring image."""
    embed = discord.Embed(
        title=f"⏱️ {timer.minutes}-minute timer",
        description=_timer_text(timer),
        colour=_PHASE_COLOURS[timer.phase],
    )
    if not timer.over:
        embed.set_footer(text=TIMER_FOOTER)
    if with_ring:
        embed.set_image(url=f"attachment://{countdown_ring.FILENAME}")
    return embed


async def draw_user_timer_ring(timer: UserTimer, as_of: datetime) -> discord.File | None:
    """Draw the ring for the timer's state at `as_of`, or None if drawing fails.

    The timer carries on with text only then, as the channel's timer does.
    """
    colour = _PHASE_COLOURS[timer.phase].to_rgb()
    try:
        fraction_left, headline, caption = user_timer_face(timer, as_of)
        png = await asyncio.to_thread(countdown_ring.render_ring, fraction_left, colour, headline, caption)
    except Exception:
        log.exception("Could not draw the /timer ring for channel %s.", timer.channel_id)
        return None
    return discord.File(io.BytesIO(png), filename=countdown_ring.FILENAME)


class UserTimerService:
    """Runs the timers people set with /timer, at most one per temp VC."""

    def __init__(self, channels: ChannelManager, speaker: Speaker) -> None:
        self.channels = channels
        self.speaker = speaker

    # ----- Setting one up ---------------------------------------------------

    def create(self, session: TempVCSession, set_by: int, minutes: int) -> UserTimer:
        """Make a timer for a channel, ready to post.

        Nothing here is awaited, so two people using /timer at the same moment
        can't both make one.
        """
        timer = UserTimer(channel_id=session.channel_id, set_by=set_by, minutes=minutes)
        timer.view = UserTimerView(self, timer)
        session.user_timer = timer
        return timer

    async def post(self, timer: UserTimer) -> None:
        """Post the timer in its channel's chat. Raises an error if that fails."""
        channel = self.channels.voice_channel(timer.channel_id)
        if channel is None:
            raise ChannelGoneError(f"Channel {timer.channel_id} no longer exists.")
        embed, files, view = await self._render(timer, discord.utils.utcnow())
        assert view is not None, "A new timer always has its buttons."
        message = await channel.send(embed=embed, files=files, view=view)
        timer.message_id = message.id

    def discard(self, timer: UserTimer) -> None:
        """Drop a timer without touching its message: it couldn't be posted, or it was deleted."""
        timer.stop_background_work()
        self._forget(timer)

    # ----- The buttons ------------------------------------------------------
    # These check the phase first, because someone else may have just clicked.

    async def start(self, timer: UserTimer, click: discord.Interaction) -> None:
        """Count in, then start the countdown, or carry it on after a pause.

        The click is answered by showing "get ready" straight away. That edit goes
        through the click rather than the channel, so it doesn't count towards
        Discord's limit of about 5 edits per 5 seconds in a channel. The count-in's
        3, 2, 1 and start already make 4.
        """
        async with timer.message_lock:  # An edit from Pause may still be on its way.
            if timer.phase not in (UserTimerPhase.READY, UserTimerPhase.PAUSED):
                await click.response.defer()  # Someone else got there first.
                return
            timer.count_in()
            # Started inside the lock, so its first edit waits until this one is done.
            timer.task = asyncio.create_task(self._run(timer), name=f"user-timer-{timer.channel_id}")
            embed, files, view = await self._render(timer, discord.utils.utcnow())
            await click.response.edit_message(embed=embed, attachments=files, view=view)

    async def pause(self, timer: UserTimer) -> None:
        """Hold the time left. Works during the count-in too."""
        # With the message lock, so the task is never cancelled halfway through an edit.
        # That edit could still reach Discord after this one, and undo it.
        async with timer.message_lock:
            if timer.phase not in (UserTimerPhase.COUNTING_IN, UserTimerPhase.RUNNING):
                return
            timer.pause(discord.utils.utcnow())
            timer.cancel_task()
        await self.refresh(timer)

    async def stop(self, timer: UserTimer) -> None:
        """End the timer for good, without a ring. Its buttons go, and the channel can have a new one."""
        async with timer.message_lock:  # For the same reason as pause.
            if timer.over:
                return
            timer.stop(discord.utils.utcnow())
            timer.stop_background_work()
        await self._wind_up(timer)

    # ----- Showing it -------------------------------------------------------

    async def refresh(self, timer: UserTimer, *, as_of: datetime | None = None) -> None:
        """Show the timer's state in its message, with the time left at `as_of` (default: now)."""
        # One edit at a time, each drawn from the state at the moment it's sent, so a
        # slow edit can't land after a newer one and undo it.
        async with timer.message_lock:
            channel = self.channels.voice_channel(timer.channel_id)
            if channel is None or timer.message_id is None:
                return
            embed, files, view = await self._render(timer, as_of or discord.utils.utcnow())
            try:
                message = channel.get_partial_message(timer.message_id)
                # Listing only the new ring replaces the previous one. No view removes the buttons.
                await message.edit(embed=embed, attachments=files, view=view)
            except API_ERRORS as error:
                log.warning("Could not update the /timer message in channel %s: %s", timer.channel_id, error)

    async def _render(
        self, timer: UserTimer, as_of: datetime
    ) -> tuple[discord.Embed, list[discord.File], UserTimerView | None]:
        """The timer's embed, ring and buttons at `as_of`. A timer that's over has no buttons."""
        ring = await draw_user_timer_ring(timer, as_of)
        embed = build_user_timer_embed(timer, with_ring=ring is not None)
        view = None
        if not timer.over and timer.view is not None:
            timer.view.sync()
            view = timer.view
        return embed, [ring] if ring else [], view

    # ----- Running it -------------------------------------------------------

    async def _run(self, timer: UserTimer) -> None:
        """Count in, count down and ring. Runs as a background task."""
        try:
            await self._count_in(timer)
            await self._count_down(timer)
        except Exception:
            # Cancelling isn't an Exception, so Pause and Stop skip this.
            log.exception("The /timer in channel %s failed, so it has been stopped.", timer.channel_id)
            if not timer.over:
                timer.stop(discord.utils.utcnow())
                await self._wind_up(timer)

    async def _count_in(self, timer: UserTimer) -> None:
        """Show 3, 2, 1, a beat apart, beeping on each, then start the countdown."""
        beat = timedelta(seconds=COUNT_IN_BEAT_SECONDS)
        sound = await asyncio.to_thread(sounds.count_in, COUNT_IN_FROM, COUNT_IN_BEAT_SECONDS)
        channel = self.channels.voice_channel(timer.channel_id)
        ready_by = discord.utils.utcnow() + timedelta(seconds=SOUND_JOIN_SECONDS)
        async with self.speaker.joined(channel, by=ready_by) as voice:
            if voice is not None:
                voice.start(sound)
            # The sound times its own beeps. The numbers are shown on the same schedule,
            # from the moment it started, so a slow edit can't push the rest later.
            first_beep = discord.utils.utcnow() + timedelta(seconds=sounds.LEAD_IN_SECONDS)
            for index, number in enumerate(range(COUNT_IN_FROM, 0, -1)):
                await discord.utils.sleep_until(first_beep + index * beat)
                timer.count = number
                await self.refresh(timer)
            go = first_beep + COUNT_IN_FROM * beat
            await discord.utils.sleep_until(go)
            timer.run(go)
            await self.refresh(timer, as_of=go)
            if voice is not None:
                await voice.wait()  # The higher beep at the start, before the bot leaves.

    async def _count_down(self, timer: UserTimer) -> None:
        """Redraw the ring until the time is up, then show that, and ring."""
        assert timer.ends_at is not None, "The countdown starts when the timer runs."
        ends_at = timer.ends_at
        # The ring runs alongside, so joining voice can never make the timer finish late.
        ring = asyncio.create_task(self._ring(timer, ends_at), name=f"user-timer-ring-{timer.channel_id}")
        try:
            async with self._redrawing(timer, ends_at):
                await discord.utils.sleep_until(ends_at)
            timer.finish()
            await self._wind_up(timer)
            await ring
        finally:
            # Pausing or stopping cancels the ring too, and the bot leaves voice.
            ring.cancel()

    async def _ring(self, timer: UserTimer, ends_at: datetime) -> None:
        """Join the channel shortly before `ends_at`, and ring once the time is up."""
        join_time = timedelta(seconds=SOUND_JOIN_SECONDS)
        sound = await asyncio.to_thread(sounds.ring, RING_SECONDS)
        await discord.utils.sleep_until(ends_at - join_time)
        channel = self.channels.voice_channel(timer.channel_id)
        # If the bot is busy ringing in another channel, a ring a few seconds late
        # is better than none, so it waits past the end for its turn.
        async with self.speaker.joined(channel, by=ends_at + join_time) as voice:
            await discord.utils.sleep_until(ends_at)
            if voice is not None:
                await voice.play(sound)

    @asynccontextmanager
    async def _redrawing(self, timer: UserTimer, deadline: datetime) -> AsyncIterator[None]:
        """Redraw the ring on schedule until `deadline`, while the `async with` block runs.

        The redraws run as their own task, so a slow edit never holds up the countdown.
        """
        redraws = asyncio.create_task(self._redraw_until(timer, deadline), name=f"user-timer-redraw-{timer.channel_id}")
        try:
            yield
        finally:
            redraws.cancel()

    async def _redraw_until(self, timer: UserTimer, deadline: datetime) -> None:
        step = minutes_to_delta(USER_TIMER_REDRAW_SECONDS / 60)
        # Never from before the countdown began, which was drawn already. asyncio can
        # wake a few milliseconds early, so the clock can be just before that moment.
        began = deadline - timer.remaining
        for moment in redraw_times(deadline, max(discord.utils.utcnow(), began), step):
            await discord.utils.sleep_until(moment)
            # As of the planned moment, so the time shown is a whole step, not a hair more.
            await self.refresh(timer, as_of=moment)

    async def _wind_up(self, timer: UserTimer) -> None:
        """Show a timer that has finished or stopped, without buttons, and free the channel for a new one."""
        timer.stop_background_work()  # Leaves the current task alone, so a ring carries on.
        self._forget(timer)
        await self.refresh(timer)

    def _forget(self, timer: UserTimer) -> None:
        session = self.channels.sessions.by_channel(timer.channel_id)
        if session is not None and session.user_timer is timer:
            session.user_timer = None
