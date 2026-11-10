"""Reply to anyone who chats in the topic channel, pointing them to /topic."""

from __future__ import annotations

import logging
import time

import discord

from ..constants import TOPIC_CHAT_REPLY_MESSAGE, TOPIC_CHAT_REPLY_SECONDS
from ..errors import API_ERRORS

log = logging.getLogger(__name__)

# Messages people write, either on their own or as a reply. Others, such as
# "pinned a message", come from Discord itself.
_TYPED_MESSAGE_TYPES = (discord.MessageType.default, discord.MessageType.reply)


class ChatReminder:
    """Replies to every message people post in the topic channel.

    The bot can't read what people write, because that needs the privileged
    Message Content intent, so every message gets the same reply. Nobody's
    message is deleted.
    """

    def __init__(self) -> None:
        # Replaced with a clickable mention, such as </topic:123>, once /topic is
        # registered. Clicking one fills in the command.
        self.command_mention = "/topic"
        # Member ID -> when the reply to them is deleted. While it's showing, their
        # next messages get no other, so posting ten times doesn't bring ten replies.
        self._showing_until: dict[int, float] = {}

    async def reply_to(self, message: discord.Message) -> None:
        """Reply to a message posted in the topic channel, unless it's from a bot or Discord itself."""
        if message.author.bot or message.webhook_id is not None or message.type not in _TYPED_MESSAGE_TYPES:
            return
        now = time.monotonic()
        self._showing_until = {member_id: until for member_id, until in self._showing_until.items() if until > now}
        if message.author.id in self._showing_until:
            return
        # Noted before sending, so messages arriving during the send don't get replies too.
        self._showing_until[message.author.id] = now + TOPIC_CHAT_REPLY_SECONDS
        try:
            await message.channel.send(
                TOPIC_CHAT_REPLY_MESSAGE.format(command=self.command_mention),
                # Still sent if they deleted their message in the meantime, just not as a reply.
                reference=message.to_reference(fail_if_not_exists=False),
                # A reply pings the person it replies to only when asked.
                mention_author=True,
                delete_after=TOPIC_CHAT_REPLY_SECONDS,
            )
        except API_ERRORS as error:
            log.warning("Could not reply to a message in the topic channel: %s", error)
