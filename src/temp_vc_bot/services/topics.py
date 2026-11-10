"""Topics started with /topic: who's interested, and opening a VC for them.

Like sessions, topics live only in memory. If the bot restarts, the buttons on
cards posted before then stop working, and their hosts start a new topic.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import discord

from ..constants import (
    ALREADY_OWN_VC_MESSAGE,
    INVITES_PER_MESSAGE,
    TOPIC_HAS_VC_MESSAGE,
    TOPIC_INVITE_MESSAGE,
    TOPIC_JOIN_DEADLINE_MINUTES,
    TOPIC_VC_BEING_CREATED_MESSAGE,
    minutes_to_delta,
)
from ..errors import API_ERRORS
from ..state import TempVCSession
from .channel_manager import ChannelManager, human_members
from .session_setup import SessionSetup

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Topic:
    """One topic, from the moment its form is sent until it closes."""

    host_id: int
    name: str  # The VC's name.
    status: str  # The line Discord shows under the VC's name.
    text: str  # What the host wants to discuss.
    # Everyone who clicked "I'm interested", in the order they did. A dict keeps
    # that order and makes each person count once.
    interested_ids: dict[int, None] = field(default_factory=dict)
    vc_channel_id: int | None = None
    # True while the VC is being created, so a double click makes only one.
    creating_vc: bool = False
    # True once the public card stops taking clicks without a VC being made.
    closed: bool = False

    def toggle_interest(self, member_id: int) -> bool:
        """Add or remove someone's interest. Returns True if they're now interested."""
        if member_id in self.interested_ids:
            del self.interested_ids[member_id]
            return False
        self.interested_ids[member_id] = None
        return True


class CannotOpenVC(Exception):
    """The topic's VC can't be created right now. The message says why, for the host."""


def channel_link(channel_id: int) -> str:
    """A mention of a channel. Clicking one for a voice channel joins it."""
    return f"<#{channel_id}>"


class TopicService:
    """Opens a topic's VC and invites everyone interested."""

    def __init__(self, channels: ChannelManager, setup: SessionSetup) -> None:
        self.channels = channels
        self.setup = setup

    async def open_vc(self, topic: Topic, host: discord.Member) -> TempVCSession:
        """Create the topic's VC, move the host in if they're in voice, and invite everyone interested.

        Raises CannotOpenVC if the topic already has a VC or the host owns one, and
        Discord's errors if the channel can't be created.
        """
        if topic.vc_channel_id is not None:
            raise CannotOpenVC(TOPIC_HAS_VC_MESSAGE.format(channel=channel_link(topic.vc_channel_id)))
        if topic.creating_vc:
            raise CannotOpenVC(TOPIC_VC_BEING_CREATED_MESSAGE)
        topic.creating_vc = True
        try:
            session = await self._create(topic, host)
        finally:
            topic.creating_vc = False
        topic.vc_channel_id = session.channel_id
        log.info("Opened VC %s for a topic by member %s.", session.channel_id, host.id)

        # The host chose the name already, so the naming deadline doesn't apply.
        session.named.set()
        await self._set_status(session, topic.status)
        moved_in = await self._move_in_if_in_voice(host, session)
        if not moved_in:
            self._start_join_deadline(session)
        await self.setup.finish(host, session, ask_for_name=False)
        await self._invite(topic, session)
        return session

    async def _create(self, topic: Topic, host: discord.Member) -> TempVCSession:
        """Create the channel, unless the host already owns one."""
        async with self.channels.creation_lock:
            existing = self.channels.sessions.by_owner(host.id)
            if existing is not None:
                raise CannotOpenVC(ALREADY_OWN_VC_MESSAGE.format(channel=channel_link(existing.channel_id)))
            return await self.channels.create_session(host, name=topic.name)

    async def _set_status(self, session: TempVCSession, status: str) -> None:
        """Set the VC's status. The channel still works without it, so a failure is only logged."""
        try:
            await self.channels.set_status(session, status)
        except API_ERRORS as error:
            log.warning("Could not set the status of topic VC %s: %s", session.channel_id, error)

    async def _move_in_if_in_voice(self, host: discord.Member, session: TempVCSession) -> bool:
        """Move the host into their new VC if they're in another voice channel. Returns whether they're in.

        Discord can only move people who are already in voice. Anyone else joins
        by clicking the channel.
        """
        guild = self.channels.guild
        # The bot's own copy of the member, which knows their voice channel. Without
        # the Members intent, the bot only keeps copies of people in voice.
        member = guild.get_member(host.id) if guild else None
        if member is None or member.voice is None or member.voice.channel is None:
            return False
        return await self.channels.move_member(member, session)

    def _start_join_deadline(self, session: TempVCSession) -> None:
        """Delete the VC if nobody joins it in time.

        Nobody is inside, so no one leaving would ever trigger the usual cleanup.
        """
        seconds = minutes_to_delta(TOPIC_JOIN_DEADLINE_MINUTES).total_seconds()
        session.join_task = asyncio.create_task(
            self._delete_if_nobody_joins(session, seconds), name=f"join-{session.channel_id}"
        )

    async def _delete_if_nobody_joins(self, session: TempVCSession, seconds: float) -> None:
        """Wait out the deadline, then delete the VC if it's empty.

        Anyone who joined and then left has already closed it the usual way, so an
        empty channel here means nobody came.
        """
        await asyncio.sleep(seconds)
        channel = self.channels.get_channel(session)
        if channel is None or not human_members(channel):
            await self.channels.close_session(session, "nobody joined it in time")

    async def _invite(self, topic: Topic, session: TempVCSession) -> None:
        """Ping everyone interested in the topic channel, with a link to the VC."""
        channel = self.channels.topic_channel
        member_ids = list(topic.interested_ids)
        if channel is None or not member_ids:
            return
        name = discord.utils.escape_markdown(topic.name)
        for start in range(0, len(member_ids), INVITES_PER_MESSAGE):
            batch = member_ids[start : start + INVITES_PER_MESSAGE]
            text = TOPIC_INVITE_MESSAGE.format(
                mentions=" ".join(f"<@{member_id}>" for member_id in batch),
                name=name,
                channel=channel_link(session.channel_id),
            )
            try:
                # Only the people named here are pinged. A topic name such as
                # "@everyone" pings nobody.
                mentions = discord.AllowedMentions(users=[discord.Object(member_id) for member_id in batch])
                await channel.send(text, allowed_mentions=mentions)
            except API_ERRORS as error:
                log.warning("Could not invite people to topic VC %s: %s", session.channel_id, error)
                return
