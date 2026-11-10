"""Run the real bot against the fake Discord for /timer."""

from __future__ import annotations

from typing import Any

import pytest

from temp_vc_bot import constants, countdown_ring
from temp_vc_bot.bot import TempVCBot
from temp_vc_bot.services import naming
from temp_vc_bot.services import timer as channel_timer
from temp_vc_bot.services import user_timer as user_timer_service
from temp_vc_bot.state import UserTimer, UserTimerPhase

from .fake_discord import (
    BOT_USER_ID,
    GUILD_ID,
    HUB_ID,
    TOPIC_CHANNEL_ID,
    FakeDiscord,
    find_components,
)

BEAT = 0.05  # Seconds between the count-in's numbers in these tests.
LONG = 60  # Long enough to keep running for a whole test: 72 seconds at this speed.
# The captions only a /timer's ring uses, to tell its drawings from the channel timer's.
USER_TIMER_CAPTIONS = {"ready", "get ready", "left", "paused", "time's up", "stopped"}


@pytest.fixture(autouse=True)
def quick_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a 1-minute /timer take 1.2 seconds, and the count-in a fraction of a second.

    The ring's scheduled redraws are off, as they would race with the tests' clicks.
    TestRedraws turns them on. The channel's own timer, its reposts and its naming
    deadline stay out of the way.
    """
    monkeypatch.setattr(constants, "SECONDS_PER_MINUTE", 1.2)
    monkeypatch.setattr(user_timer_service, "COUNT_IN_BEAT_SECONDS", BEAT)
    monkeypatch.setattr(user_timer_service, "USER_TIMER_REDRAW_SECONDS", 1000)
    monkeypatch.setattr(channel_timer, "TIMER_REPOST_DELAY_SECONDS", 1000)
    monkeypatch.setattr(naming, "NAME_DEADLINE_MINUTES", 1000)


@pytest.fixture
def faces(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every (big text, small text) drawn on a /timer's ring, in order."""
    drawn: list[tuple[str, str]] = []
    real_render = countdown_ring.render_ring

    def recording_render(fraction_left: float, colour: Any, headline: str, caption: str) -> bytes:
        if caption in USER_TIMER_CAPTIONS:
            drawn.append((headline, caption))
        return real_render(fraction_left, colour, headline, caption)

    monkeypatch.setattr(countdown_ring, "render_ring", recording_render)
    return drawn


async def make_vc(world: FakeDiscord, name: str = "Alice") -> tuple[int, int]:
    """A member joins the hub and is moved into a new channel. Returns both IDs."""
    existing = {int(c["id"]) for c in world.temp_channels()}
    owner = world.add_member(name)
    await world.join_voice(owner, HUB_ID)
    await world.settle()
    channel_id = next(int(c["id"]) for c in world.temp_channels() if int(c["id"]) not in existing)
    return owner, channel_id


def timer_card(world: FakeDiscord, channel_id: int) -> dict[str, Any]:
    cards = [m for m in world.messages_in(channel_id) if m["embeds"] and "-minute timer" in m["embeds"][0]["title"]]
    assert len(cards) == 1, f"Expected one /timer message, found {len(cards)}."
    return cards[0]


def buttons(message: dict[str, Any]) -> dict[str, bool]:
    """Each button's label, and whether it can be clicked."""
    return {button["label"]: not button.get("disabled", False) for button in find_components(message, "type", 2)}


def text(message: dict[str, Any]) -> str:
    return message["embeds"][0]["description"]


def the_timer(bot: TempVCBot, channel_id: int) -> UserTimer:
    session = bot.sessions.by_channel(channel_id)
    assert session is not None and session.user_timer is not None
    return session.user_timer


async def set_timer(world: FakeDiscord, bot: TempVCBot, minutes: int = 1) -> tuple[int, int, UserTimer]:
    """Make a channel and set a timer in it. Returns the owner, the channel and the timer."""
    owner, channel_id = await make_vc(world)
    await world.use_command(owner, channel_id, "timer", minutes=minutes)
    return owner, channel_id, the_timer(bot, channel_id)


async def start_and_wait_until_running(world: FakeDiscord, member: int, channel_id: int, timer: UserTimer) -> None:
    await world.click(member, timer_card(world, channel_id), "Start")
    await world.wait_for(lambda: timer.phase is UserTimerPhase.RUNNING)
    await world.settle()


class TestTimerCommand:
    async def test_it_asks_for_whole_minutes_in_a_range(self, world: FakeDiscord, bot: TempVCBot) -> None:
        command = next(c for c in world.commands[GUILD_ID] if c["name"] == "timer")
        [option] = command["options"]
        assert (option["name"], option["type"], option["required"]) == ("minutes", 4, True)  # 4 is a whole number.
        assert (option["min_value"], option["max_value"]) == (1, 60)

    async def test_it_posts_a_timer_with_a_full_ring_and_three_buttons(self, world: FakeDiscord, bot: TempVCBot, faces: list[tuple[str, str]]) -> None:
        owner, channel_id = await make_vc(world)
        record = await world.use_command(owner, channel_id, "timer", minutes=5)
        assert record.private_texts() == [constants.TIMER_POSTED_MESSAGE]
        card = timer_card(world, channel_id)
        assert card["embeds"][0]["title"] == "⏱️ 5-minute timer"
        assert f"Set by <@{owner}>" in text(card)
        assert buttons(card) == {"Start": True, "Pause": False, "Stop": True}
        assert len(card["attachments"]) == 1
        assert faces == [("5:00", "ready")]

    @pytest.mark.parametrize("where", [HUB_ID, TOPIC_CHANNEL_ID])
    async def test_it_only_works_in_a_temp_vc(self, world: FakeDiscord, bot: TempVCBot, where: int) -> None:
        member = world.add_member("Alice")
        await world.join_voice(member, HUB_ID)
        record = await world.use_command(member, where, "timer", minutes=5)
        assert record.private_texts() == [constants.TIMER_NOT_TEMP_VC_MESSAGE]
        assert not [m for m in world.messages.values() if m["embeds"] and "minute timer" in m["embeds"][0]["title"]]

    async def test_only_people_in_the_voice_channel_can_set_one(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        outsider = world.add_member("Bob")
        record = await world.use_command(outsider, channel_id, "timer", minutes=5)
        assert record.private_texts() == [constants.TIMER_JOIN_VOICE_MESSAGE]
        assert bot.sessions.by_channel(channel_id).user_timer is None

    async def test_a_channel_has_one_timer_at_a_time(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, _ = await set_timer(world, bot)
        record = await world.use_command(owner, channel_id, "timer", minutes=3)
        assert record.private_texts() == [constants.TIMER_EXISTS_MESSAGE]
        await world.click(owner, timer_card(world, channel_id), "Stop")
        await world.use_command(owner, channel_id, "timer", minutes=3)
        assert the_timer(bot, channel_id).minutes == 3

    @pytest.mark.parametrize("in_bulk", [False, True])
    async def test_deleting_the_timer_message_frees_the_channel(self, world: FakeDiscord, bot: TempVCBot, in_bulk: bool) -> None:
        owner, channel_id, timer = await set_timer(world, bot)
        await world.delete_message_by_hand(timer.message_id, in_bulk=in_bulk)
        await world.wait_for(lambda: bot.sessions.by_channel(channel_id).user_timer is None)
        record = await world.use_command(owner, channel_id, "timer", minutes=2)
        assert record.private_texts() == [constants.TIMER_POSTED_MESSAGE]


class TestCountIn:
    async def test_start_shows_3_2_1_then_runs(self, world: FakeDiscord, bot: TempVCBot, faces: list[tuple[str, str]]) -> None:
        owner, channel_id, timer = await set_timer(world, bot)
        await start_and_wait_until_running(world, owner, channel_id, timer)
        await world.wait_for(lambda: len(faces) >= 5)
        assert faces[:5] == [
            ("1:00", "ready"),
            ("3", "get ready"),
            ("2", "get ready"),
            ("1", "get ready"),
            ("1:00", "left"),
        ]
        card = timer_card(world, channel_id)
        await world.wait_for(lambda: text(card).startswith("Ends at <t:"))
        assert buttons(card) == {"Start": False, "Pause": True, "Stop": True}

    async def test_the_3_is_the_clicks_own_answer(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Discord allows about 5 edits per 5 seconds in a channel. Answering the click
        # doesn't count, so the count-in uses 3: the 2, the 1 and the start.
        owner, channel_id, timer = await set_timer(world, bot, minutes=LONG)
        record = await world.click(owner, timer_card(world, channel_id), "Start")
        assert [response["type"] for response in record.responses] == [7]  # 7 updates the clicked message.
        await world.wait_for(lambda: timer.phase is UserTimerPhase.RUNNING)
        await world.wait_for(lambda: world.edits_of(timer.message_id) >= 3)
        await world.settle()
        assert world.edits_of(timer.message_id) == 3

    async def test_the_bot_never_joins_voice(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, timer = await set_timer(world, bot)  # 1.2 seconds at this speed.
        await world.click(owner, timer_card(world, channel_id), "Start")
        await world.wait_for(lambda: timer.phase is UserTimerPhase.FINISHED)
        await world.settle()
        assert BOT_USER_ID not in world.voice
        assert not world.bot_left_voice_itself, "The bot should never have joined, so never left either."
        assert bot.channel_manager.guild.voice_client is None


class TestButtons:
    async def test_pause_holds_the_time_and_start_carries_on(self, world: FakeDiscord, bot: TempVCBot, faces: list[tuple[str, str]]) -> None:
        owner, channel_id, timer = await set_timer(world, bot, minutes=LONG)
        await start_and_wait_until_running(world, owner, channel_id, timer)
        await world.click(owner, timer_card(world, channel_id), "Pause")
        assert timer.phase is UserTimerPhase.PAUSED
        assert timer.length * 0.8 < timer.remaining <= timer.length
        card = timer_card(world, channel_id)
        await world.wait_for(lambda: text(card).startswith("Paused."))
        assert buttons(card) == {"Start": True, "Pause": False, "Stop": True}
        assert faces[-1][1] == "paused"
        held = timer.remaining

        drawn_before = len(faces)
        await start_and_wait_until_running(world, owner, channel_id, timer)
        assert ("3", "get ready") in faces[drawn_before:], "Carrying on counts in again."
        assert timer.ends_at is not None
        assert timer.time_left(timer.ends_at - held) == held, "It carries on from the time it was paused at."

    async def test_pause_during_the_count_in_goes_back_to_ready(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(user_timer_service, "COUNT_IN_BEAT_SECONDS", 2.0)  # Time to click.
        owner, channel_id, timer = await set_timer(world, bot)
        await world.click(owner, timer_card(world, channel_id), "Start")
        assert timer.count == 3
        await world.click(owner, timer_card(world, channel_id), "Pause")
        assert timer.phase is UserTimerPhase.READY
        assert timer.remaining == timer.length
        card = timer_card(world, channel_id)
        await world.wait_for(lambda: text(card).startswith("Click **Start**"))
        assert buttons(card) == {"Start": True, "Pause": False, "Stop": True}

    async def test_stop_ends_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, timer = await set_timer(world, bot, minutes=LONG)
        await start_and_wait_until_running(world, owner, channel_id, timer)
        await world.click(owner, timer_card(world, channel_id), "Stop")
        assert timer.phase is UserTimerPhase.STOPPED
        card = timer_card(world, channel_id)
        await world.wait_for(lambda: text(card).startswith("Stopped."))
        assert card["components"] == [], "A stopped timer has no buttons."
        assert bot.sessions.by_channel(channel_id).user_timer is None

    async def test_stop_before_starting(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, timer = await set_timer(world, bot)
        await world.click(owner, timer_card(world, channel_id), "Stop")
        assert timer.phase is UserTimerPhase.STOPPED
        await world.wait_for(lambda: timer_card(world, channel_id)["components"] == [])

    async def test_anyone_in_the_channel_can_use_the_buttons(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id, timer = await set_timer(world, bot, minutes=LONG)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await start_and_wait_until_running(world, friend, channel_id, timer)

    async def test_people_outside_the_channel_get_a_private_note(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id, timer = await set_timer(world, bot)
        outsider = world.add_member("Bob")
        record = await world.click(outsider, timer_card(world, channel_id), "Start")
        assert record.private_texts() == [constants.TIMER_JOIN_VOICE_MESSAGE]
        assert timer.phase is UserTimerPhase.READY


class TestTimeUp:
    async def test_it_shows_times_up_and_its_buttons_go(self, world: FakeDiscord, bot: TempVCBot, faces: list[tuple[str, str]]) -> None:
        owner, channel_id, timer = await set_timer(world, bot)  # 1.2 seconds at this speed.
        await world.click(owner, timer_card(world, channel_id), "Start")
        await world.wait_for(lambda: timer.phase is UserTimerPhase.FINISHED)
        card = timer_card(world, channel_id)
        await world.wait_for(lambda: text(card).startswith("⏰ Time's up!"))
        assert card["components"] == []
        assert faces[-1] == ("0:00", "time's up")
        assert bot.sessions.by_channel(channel_id).user_timer is None, "The channel can have a new timer."

    async def test_the_channel_closing_stops_the_timer(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, timer = await set_timer(world, bot, minutes=LONG)
        await start_and_wait_until_running(world, owner, channel_id, timer)
        task = timer.task
        assert task is not None
        await world.leave_voice(owner)  # The channel is empty, so it's deleted.
        await world.wait_for(lambda: channel_id not in world.channels)
        await world.wait_for(task.done)
        assert task.cancelled()


class TestRedraws:
    async def test_the_time_goes_down_in_exact_steps(self, world: FakeDiscord, bot: TempVCBot, faces: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(user_timer_service, "USER_TIMER_REDRAW_SECONDS", 10)  # 0.2 seconds at this speed.
        owner, channel_id, timer = await set_timer(world, bot)
        await world.click(owner, timer_card(world, channel_id), "Start")
        await world.wait_for(lambda: timer.phase is UserTimerPhase.FINISHED)
        await world.settle()
        running = [headline for headline, caption in faces if caption == "left"]
        assert running == ["1:00", "0:50", "0:40", "0:30", "0:20", "0:10"], faces
