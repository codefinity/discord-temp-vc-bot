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
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING

from .constants import DEFAULT_TIMER_MINUTES, EXTENSION_MINUTES, NO_USER_LIMIT, minutes_to_delta

if TYPE_CHECKING:
    from .views.control_panel import ControlPanelView


class TimerPhase(Enum):
    """The stages a channel's timer moves through, in order."""

    WAITING = auto()  # Nobody but the owner has joined yet.
    RUNNING = auto()  # The base time is counting down.
    AWAITING_ANSWER = auto()  # Time ran out. Waiting for the owner's Yes or No.
    EXTENDED = auto()  # The owner said Yes. The extra time is counting down.
    ENDING = auto()  # The channel is about to be deleted.


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
    # Stays False until the owner has been moved in and the messages are posted.
    # Until then the channel is empty because it is new, not because everyone left.
    setup_finished: bool = False
    closed: bool = False

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
        """Cancel the timer and stop listening for button clicks on the panel."""
        # A task must not cancel itself, or it would stop before deleting the channel.
        if self.timer_task is not None and self.timer_task is not asyncio.current_task():
            self.timer_task.cancel()
        if self.panel_view is not None:
            self.panel_view.stop()


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
