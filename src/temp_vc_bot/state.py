"""What the bot remembers about each temporary voice channel.

Everything here lives in memory, so it is lost on restart. That is why the bot
deletes leftover channels when it starts up.

Sessions store Discord IDs instead of channel or member objects on purpose:
discord.py can replace its objects after a long disconnect, and a stored object
would then go out of date. IDs stay valid, so the bot looks objects up when needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING

from .constants import DEFAULT_TIMER_MINUTES, EXTENSION_MINUTES, NO_USER_LIMIT, minutes_to_delta

if TYPE_CHECKING:
    from .views.control_panel import ControlPanelView
    from .views.name_prompt import NamePromptView
    from .views.user_timer import UserTimerView


class TimerPhase(Enum):
    """The stages a channel's timer moves through, in order."""

    WAITING = auto()  # Nobody but the owner has joined yet.
    RUNNING = auto()  # The base time is counting down.
    AWAITING_ANSWER = auto()  # Time ran out. Waiting for the owner's Yes or No.
    EXTENDED = auto()  # The owner said Yes. The extra time is counting down.
    ENDING = auto()  # The channel is about to be deleted.


class UserTimerPhase(Enum):
    """The stages of a timer someone set with /timer."""

    READY = auto()  # Posted. Nobody has clicked Start yet.
    COUNTING_IN = auto()  # Showing 3, 2, 1 before it starts or carries on.
    RUNNING = auto()
    PAUSED = auto()
    FINISHED = auto()  # It reached zero.
    STOPPED = auto()  # Someone clicked Stop.


@dataclass(eq=False)
class UserTimer:
    """A timer someone set with /timer in a temp VC's chat.

    It's only for the people in the channel. The channel's own timer, which
    decides when the channel closes, is separate and doesn't change.
    """

    channel_id: int
    set_by: int  # The member who used /timer.
    minutes: int
    phase: UserTimerPhase = UserTimerPhase.READY
    # The time left while it isn't running. While it runs, ends_at says when it ends.
    remaining: timedelta = field(init=False)
    ends_at: datetime | None = None
    # The number shown during the count-in, or None until the bot is ready to beep.
    count: int | None = None
    # Whether the countdown has run at all. Until it has, pausing goes back to READY.
    has_run: bool = False
    message_id: int | None = None
    view: UserTimerView | None = None
    # The count-in, countdown and ring. Pause and Stop cancel it.
    task: asyncio.Task[None] | None = None
    # Taken while editing the message, so edits go out one at a time.
    message_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.remaining = self.length

    @property
    def length(self) -> timedelta:
        return minutes_to_delta(self.minutes)

    @property
    def over(self) -> bool:
        """Whether it has finished or been stopped. Neither can be undone."""
        return self.phase in (UserTimerPhase.FINISHED, UserTimerPhase.STOPPED)

    def time_left(self, now: datetime) -> timedelta:
        """How long is left at `now`."""
        if self.phase is UserTimerPhase.RUNNING:
            assert self.ends_at is not None, "A running timer always has an end time."
            # Never more than when it started running. asyncio can wake a few
            # milliseconds early, so `now` can be just before that moment.
            return min(max(self.ends_at - now, timedelta(0)), self.remaining)
        return self.remaining

    def count_in(self) -> None:
        """Get ready to start, or to carry on after a pause."""
        self.phase = UserTimerPhase.COUNTING_IN
        self.count = None

    def run(self, now: datetime) -> None:
        """Count down what's left, from `now`."""
        self.phase = UserTimerPhase.RUNNING
        self.count = None
        self.ends_at = now + self.remaining
        self.has_run = True

    def pause(self, now: datetime) -> None:
        """Hold the time left. Pausing during the first count-in goes back to the start."""
        self.remaining = self.time_left(now)
        self.phase = UserTimerPhase.PAUSED if self.has_run else UserTimerPhase.READY
        self.ends_at = None
        self.count = None

    def finish(self) -> None:
        self.remaining = timedelta(0)
        self.phase = UserTimerPhase.FINISHED
        self.ends_at = None

    def stop(self, now: datetime) -> None:
        self.remaining = self.time_left(now)
        self.phase = UserTimerPhase.STOPPED
        self.ends_at = None
        self.count = None

    def cancel_task(self) -> None:
        """Stop the count-in, countdown or ring, whichever is happening."""
        task, self.task = self.task, None
        # A task must not cancel itself, or it would stop before it finished up.
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def stop_background_work(self) -> None:
        """Cancel its task and stop listening for clicks."""
        self.cancel_task()
        if self.view is not None:
            self.view.stop()


@dataclass(eq=False)
class TempVCSession:
    """The state of one temporary voice channel."""

    channel_id: int
    owner_id: int
    timer_minutes: int = DEFAULT_TIMER_MINUTES
    # The number of people the owner asked for. Bots get extra seats on top.
    requested_limit: int = NO_USER_LIMIT
    locked: bool = False
    phase: TimerPhase = TimerPhase.WAITING
    ends_at: datetime | None = None
    answer_deadline: datetime | None = None
    panel_message_id: int | None = None
    timer_message_id: int | None = None
    panel_view: ControlPanelView | None = None
    timer_task: asyncio.Task[None] | None = None
    # Moves the timer back to the bottom of the chat after people post below it.
    repost_task: asyncio.Task[None] | None = None
    # True when something was posted below the timer that no repost has covered yet.
    timer_buried: bool = False
    # Taken while editing or reposting the timer message, so changes go out one at a time.
    timer_message_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Stays False until the owner has been moved in and the messages are posted.
    # Until then the channel is empty because it is new, not because everyone left.
    setup_finished: bool = False
    closed: bool = False
    # Set once the owner names the channel, from the naming prompt or the panel.
    named: asyncio.Event = field(default_factory=asyncio.Event)
    # Deletes the channel if it isn't named in time.
    naming_task: asyncio.Task[None] | None = None
    name_prompt: NamePromptView | None = None
    # For a channel made from /topic, whose host may not be in voice: deletes the
    # channel if nobody joins it in time. A channel made from the hub never needs it,
    # because its owner is moved in.
    join_task: asyncio.Task[None] | None = None
    # The status the owner last set through the bot, used to fill in the rename form.
    # Discord doesn't include a channel's status with the channel, so it's kept here.
    status: str | None = None
    # The nickname with the crown that the bot gave the owner, and the one it
    # replaced. crowned_nick is None while the owner has no crown from the bot.
    crowned_nick: str | None = None
    original_nick: str | None = None
    # The timer someone set with /timer, if there is one that hasn't finished or stopped.
    user_timer: UserTimer | None = None

    @property
    def waiting_for_first_join(self) -> bool:
        """Whether the channel may still be empty because it's new, not because everyone left."""
        return self.join_task is not None and not self.join_task.done()

    @property
    def timer_started(self) -> bool:
        """Whether the countdown has begun."""
        return self.phase is not TimerPhase.WAITING

    def start_timer(self, now: datetime) -> None:
        """Begin the countdown using the length the owner chose."""
        self.phase = TimerPhase.RUNNING
        self.ends_at = now + minutes_to_delta(self.timer_minutes)

    def await_answer(self, deadline: datetime) -> None:
        """Note that the base time is over and the owner has until `deadline` to answer."""
        self.phase = TimerPhase.AWAITING_ANSWER
        self.answer_deadline = deadline

    def extend(self) -> None:
        """Add the extra time to the original end time.

        The time is added to `ends_at`, not to the moment the owner clicked Yes.
        Otherwise the time spent answering would stretch the channel past its
        maximum lifetime.
        """
        assert self.ends_at is not None, "A timer must start before it can be extended."
        self.phase = TimerPhase.EXTENDED
        self.ends_at += minutes_to_delta(EXTENSION_MINUTES)

    def stop_background_work(self) -> None:
        """Cancel the timers, reposts and both deadlines, and stop listening for clicks."""
        for task in (self.timer_task, self.repost_task, self.naming_task, self.join_task):
            # A task must not cancel itself, or it would stop before deleting the channel.
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        for view in (self.panel_view, self.name_prompt):
            if view is not None:
                view.stop()
        if self.user_timer is not None:
            self.user_timer.stop_background_work()


class SessionRegistry:
    """All live sessions, findable by channel or by owner."""

    def __init__(self) -> None:
        self._by_channel: dict[int, TempVCSession] = {}
        self._by_owner: dict[int, TempVCSession] = {}

    def add(self, session: TempVCSession) -> None:
        """Start tracking a session."""
        self._by_channel[session.channel_id] = session
        self._by_owner[session.owner_id] = session

    def remove(self, session: TempVCSession) -> bool:
        """Stop tracking a session. Returns False if it had already been removed."""
        if self._by_channel.pop(session.channel_id, None) is None:
            return False
        self._by_owner.pop(session.owner_id, None)
        return True

    def by_channel(self, channel_id: int | None) -> TempVCSession | None:
        """Find the session for a channel, if it is a temp VC."""
        return self._by_channel.get(channel_id) if channel_id is not None else None

    def by_owner(self, owner_id: int) -> TempVCSession | None:
        """Find the session this member owns, if any."""
        return self._by_owner.get(owner_id)

    def __iter__(self) -> Iterator[TempVCSession]:
        # Loop over a copy, because sessions can be removed during the loop.
        return iter(list(self._by_channel.values()))
