"""Join a voice channel for a moment to play a sound, then leave.

The bot otherwise stays out of voice, so it takes no seat and isn't listed in any
channel. Discord allows a bot in only one voice channel per server, so sounds for
different channels take turns.

Sound is a nice extra, never a requirement: if anything about voice fails, the
caller gets None and carries on silently.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import discord

from ..errors import API_ERRORS

log = logging.getLogger(__name__)


def opus_loaded() -> bool:
    """Whether the Opus library is available. Discord voice needs it to send sound.

    discord.py brings its own copy on Windows. Elsewhere it has to be installed:
    libopus0 on Ubuntu or Debian, or opus from Homebrew on a Mac.
    """
    if discord.opus.is_loaded():
        return True
    try:
        # Making an encoder loads the library, if it can be found.
        discord.opus.Encoder()
    except discord.opus.OpusNotLoaded:
        return False
    return True


def _seconds_until(moment: datetime) -> float:
    return (moment - discord.utils.utcnow()).total_seconds()


def _finish(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class Playback:
    """Plays sounds in the voice channel the bot has joined."""

    def __init__(self, client: discord.VoiceClient) -> None:
        self._client = client
        self._finished: asyncio.Future[None] | None = None

    def start(self, pcm: bytes) -> None:
        """Start playing a sound from sounds.py, and return straight away."""
        loop = asyncio.get_running_loop()
        finished = self._finished = loop.create_future()
        channel_id = self._client.channel.id

        def after(error: Exception | None) -> None:
            # discord.py calls this from its audio thread when the sound ends.
            if error is not None:
                log.warning("A sound stopped early in channel %s: %r", channel_id, error)
            try:
                loop.call_soon_threadsafe(_finish, finished)
            except RuntimeError:
                pass  # The bot is shutting down, so nobody is waiting any more.

        try:
            self._client.play(discord.PCMAudio(io.BytesIO(pcm)), after=after)
        except API_ERRORS as error:
            # Most likely the bot was disconnected a moment ago.
            log.warning("Could not play a sound in channel %s: %r", channel_id, error)
            _finish(finished)

    async def wait(self) -> None:
        """Wait until the sound started last has finished."""
        if self._finished is not None:
            await self._finished

    async def play(self, pcm: bytes) -> None:
        """Play a sound and wait until it has finished."""
        self.start(pcm)
        await self.wait()


class Speaker:
    """Takes the bot into a voice channel to play sounds, one channel at a time."""

    def __init__(self) -> None:
        # Held from joining a channel until leaving it, so channels take turns.
        self._turn = asyncio.Lock()

    def report_setup_problems(self) -> None:
        """Log a clear message if the bot can't play sound at all."""
        if not opus_loaded():
            log.warning(
                "The Opus library isn't installed, so /timer can't beep or ring. Everything else works. "
                "On Ubuntu or Debian, run: sudo apt install libopus0. On a Mac: brew install opus. "
                "Then restart the bot."
            )

    @asynccontextmanager
    async def joined(self, channel: discord.VoiceChannel | None, *, by: datetime) -> AsyncIterator[Playback | None]:
        """Join `channel` for the `async with` block, to play sounds in it, then leave.

        Gives None instead of a Playback if the bot can't be in the channel by `by`,
        because another channel is taking its turn, joining is slow, or sound isn't
        set up. The caller then carries on silently, so sound never holds anything up.
        """
        if channel is None or not self._can_play_in(channel) or not await self._take_turn(by):
            yield None
            return
        try:
            client = await self._connect(channel, by)
            yield Playback(client) if client is not None else None
        finally:
            # Nested, so the turn is passed on even if leaving is interrupted.
            try:
                await self._leave(channel.guild)
            finally:
                self._turn.release()

    def _can_play_in(self, channel: discord.VoiceChannel) -> bool:
        """Whether the bot could play a sound in this channel at all."""
        if not opus_loaded():
            return False  # The startup log already says so.
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.connect and permissions.speak):
            log.warning("Can't play a sound in channel %s: the bot is missing Connect or Speak there.", channel.id)
            return False
        return True

    async def _take_turn(self, by: datetime) -> bool:
        """Wait for any other channel's sound to finish. Returns False if it doesn't by `by`."""
        try:
            async with asyncio.timeout(max(_seconds_until(by), 0)):
                await self._turn.acquire()
        except TimeoutError:
            log.info("Skipped a sound, because the bot was still playing one in another channel.")
            return False
        return True

    async def _connect(self, channel: discord.VoiceChannel, by: datetime) -> discord.VoiceClient | None:
        """Join the channel deafened, or return None if that doesn't work by `by`."""
        # Clear out any connection an earlier failed leave left behind.
        await self._leave(channel.guild)
        timeout = _seconds_until(by)
        if timeout <= 0:
            return None
        try:
            # It has to join the channel to speak in it. Moving Members in its overwrite
            # lets it in even when the channel is full or locked.
            return await channel.connect(timeout=timeout, reconnect=False, self_deaf=True)
        except (*API_ERRORS, RuntimeError) as error:
            # RuntimeError means a voice library such as PyNaCl or davey is missing.
            # %r shows the type of error too. A timeout's own message is empty.
            log.warning("Could not join voice channel %s to play a sound: %r", channel.id, error)
            return None

    async def _leave(self, guild: discord.Guild) -> None:
        """Leave voice, if the bot is in it."""
        client = guild.voice_client
        if client is None:
            return
        try:
            await client.disconnect(force=True)
        except API_ERRORS as error:
            log.warning("Could not leave voice channel %s: %s", getattr(client.channel, "id", None), error)
