"""Shared test setup: start the fake Discord and connect the real bot to it."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import discord
import pytest
import yarl

from temp_vc_bot import countdown_ring
from temp_vc_bot.bot import TempVCBot
from temp_vc_bot.config import Settings

from .fake_discord import CATEGORY_ID, GUILD_ID, HUB_ID, TOKEN, FakeDiscord, FakeVoiceClient

BotStarter = Callable[..., Awaitable[TempVCBot]]


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeDiscord]:
    """A running fake Discord, with discord.py pointed at it."""
    fake = FakeDiscord()
    await fake.start()
    monkeypatch.setattr(discord.http.Route, "BASE", fake.rest_url)
    monkeypatch.setattr(discord.gateway.DiscordWebSocket, "DEFAULT_GATEWAY", yarl.URL(fake.gateway_url))
    # Use the real channel.connect() code, but skip the audio connection.
    monkeypatch.setitem(discord.abc.Connectable.connect.__kwdefaults__, "cls", FakeVoiceClient)

    # discord.py waits 5 seconds before logging in again, which would slow reconnect tests.
    async def no_login_delay(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(discord.Client, "before_identify_hook", no_login_delay)
    # Draw tiny rings. A full-size one takes long enough that world.settle() could
    # mistake the pause for the bot having finished. test_units checks full size.
    monkeypatch.setattr(countdown_ring, "SIZE", 64)
    monkeypatch.setattr(countdown_ring, "SUPERSAMPLE", 1)
    yield fake
    await fake.stop()


@pytest.fixture
async def start_bot(world: FakeDiscord) -> AsyncIterator[BotStarter]:
    """Start the real bot against the fake Discord. Tests call this when ready."""
    running: list[tuple[TempVCBot, asyncio.Task[None]]] = []

    async def start(*, guild_id: int = GUILD_ID) -> TempVCBot:
        bot = TempVCBot(Settings(TOKEN, guild_id, HUB_ID, CATEGORY_ID))
        bot._connection.guild_ready_timeout = 0.01  # Don't wait 2 seconds on every test.
        await bot.login(TOKEN)
        task = asyncio.create_task(bot.connect())
        running.append((bot, task))
        await asyncio.wait_for(bot.wait_until_ready(), timeout=15)
        await world.settle()
        return bot

    yield start

    for bot, task in running:
        await bot.close()
        await task


@pytest.fixture
async def bot(start_bot: BotStarter) -> TempVCBot:
    """The bot, already connected, in a server with no temp VCs yet."""
    return await start_bot()
