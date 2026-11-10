"""Run the real bot against the fake Discord for /topic, one requirement at a time."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
import pytest

from temp_vc_bot.bot import TempVCBot
from temp_vc_bot.constants import MAX_INTERESTED_SHOWN, TOPIC_CHANNEL_DESCRIPTION
from temp_vc_bot.services import topics
from temp_vc_bot.services.topics import Topic
from temp_vc_bot.state import TimerPhase
from temp_vc_bot.views import topic_ad, topic_draft
from temp_vc_bot.views.topic_card import interested_list

from .conftest import BotStarter
from .fake_discord import (
    BOT_ROLE_ID,
    EPHEMERAL_FLAG,
    GUILD_ID,
    HUB_ID,
    MANAGE_CHANNELS,
    SEND_MESSAGES,
    TEXT_INPUT,
    TOPIC_CHANNEL_ID,
    FakeDiscord,
    InteractionRecord,
    find_components,
)

NAME, STATUS, TEXT = "Exam revision", "Revising", "Going through past maths papers"
OPEN_MESSAGE = "is open, and you said you're interested"


@pytest.fixture
async def bot(world: FakeDiscord, start_bot: BotStarter) -> TempVCBot:
    """The bot, in a server that also has an ordinary voice channel called Lounge.

    The Lounge is added before the bot starts, so the bot knows about it.
    """
    world.add_channel("Lounge")
    return await start_bot()


def lounge(world: FakeDiscord) -> int:
    return next(int(c["id"]) for c in world.channels.values() if c["name"] == "Lounge")


async def start_topic(world: FakeDiscord, host: int, *values: str) -> InteractionRecord:
    """The host uses /topic in the topic channel and sends the form.

    The returned record's reply is the private card.
    """
    command = await world.use_command(host, TOPIC_CHANNEL_ID, "topic")
    values = values or (NAME, STATUS, TEXT)
    return await world.submit_modal(host, None, command.modal, *values, channel_id=TOPIC_CHANNEL_ID)


async def advertise(world: FakeDiscord, host: int) -> dict[str, Any]:
    """The host starts a topic and advertises it. Returns the public card."""
    form = await start_topic(world, host)
    await world.click(host, form.reply, "Advertise")
    return public_card(world)


def public_card(world: FakeDiscord) -> dict[str, Any]:
    cards = [m for m in world.messages_in(TOPIC_CHANNEL_ID) if m["embeds"]]
    assert len(cards) == 1, f"Expected one topic card in the channel, found {len(cards)}."
    return cards[0]


def invites(world: FakeDiscord) -> list[dict[str, Any]]:
    return [m for m in world.messages_in(TOPIC_CHANNEL_ID) if OPEN_MESSAGE in m["content"]]


def labels(message: dict[str, Any]) -> list[str]:
    return [button["label"] for button in find_components(message, "type", 2)]


def field(message: dict[str, Any], name_start: str) -> str:
    fields = message["embeds"][0].get("fields", [])
    return next(f["value"] for f in fields if f["name"].startswith(name_start))


def interested_count(message: dict[str, Any]) -> str:
    fields = message["embeds"][0]["fields"]
    return next(f["name"] for f in fields if f["name"].startswith("Interested"))


class TestTopicCommand:
    async def test_topic_is_registered_in_the_server(self, world: FakeDiscord, bot: TempVCBot) -> None:
        assert [command["name"] for command in world.commands[GUILD_ID]] == ["topic"]

    async def test_outside_the_topic_channel_only_the_user_is_told_where_to_use_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        member = world.add_member("Alice")
        record = await world.use_command(member, HUB_ID, "topic")
        assert record.private_texts() == ["/topic only works in <#3002>."]
        assert not any(response["type"] == 9 for response in record.responses)

    async def test_form_asks_for_the_name_the_status_and_the_topic(self, world: FakeDiscord, bot: TempVCBot) -> None:
        member = world.add_member("Alice")
        record = await world.use_command(member, TOPIC_CHANNEL_ID, "topic")
        boxes = find_components(record.modal, "type", TEXT_INPUT)
        assert [box["label"] for box in boxes] == ["Channel name", "Channel status", "What do you want to discuss?"]
        assert all(box.get("required", True) for box in boxes)

    async def test_the_card_is_shown_only_to_the_user(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        form = await start_topic(world, host)
        card = form.reply
        assert card is not None and card["flags"] & EPHEMERAL_FLAG
        embed = card["embeds"][0]
        assert (embed["title"], embed["description"]) == (f"💬 {NAME}", TEXT)
        assert field(card, "Status") == STATUS
        assert field(card, "Started by") == f"<@{host}>"
        assert labels(card) == ["Edit", "Advertise", "Create a VC"]
        assert world.messages_in(TOPIC_CHANNEL_ID) == []

    async def test_boxes_of_only_spaces_are_rejected(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        form = await start_topic(world, host, NAME, "   ", TEXT)
        assert form.private_texts() == ["Please fill in all three boxes. Spaces on their own don't count."]
        assert form.reply is not None and form.reply["components"] == []  # No card to click.

    async def test_edit_reopens_the_form_filled_in_and_updates_the_card(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        card = (await start_topic(world, host)).reply
        edit = await world.click(host, card, "Edit")
        assert [box.get("value") for box in find_components(edit.modal, "type", TEXT_INPUT)] == [NAME, STATUS, TEXT]
        await world.submit_modal(host, card, edit.modal, "Physics revision", "Chatting", "Forces and motion")
        embed = card["embeds"][0]
        assert (embed["title"], embed["description"]) == ("💬 Physics revision", "Forces and motion")
        assert field(card, "Status") == "Chatting"
        assert labels(card) == ["Edit", "Advertise", "Create a VC"]

    async def test_an_unused_private_card_expires(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topic_draft, "TOPIC_DRAFT_SECONDS", 0.2)
        host = world.add_member("Alice")
        card = (await start_topic(world, host)).reply
        await world.wait_for(lambda: not card["components"])
        assert card["content"] == "This draft has expired. Use /topic to start again."


class TestAdvertise:
    async def test_advertise_shows_the_card_to_everyone_in_the_chat(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        form = await start_topic(world, host)
        await world.click(host, form.reply, "Advertise")
        card = public_card(world)
        assert not card["flags"] & EPHEMERAL_FLAG
        assert card["embeds"][0]["title"] == f"💬 {NAME}"
        assert labels(card) == ["I'm interested", "Create a VC"]
        assert interested_count(card) == "Interested (0)"
        assert card["embeds"][0]["color"] == discord.Colour.red().value

    async def test_private_card_points_to_the_posted_one(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        form = await start_topic(world, host)
        await world.click(host, form.reply, "Advertise")
        assert form.reply["components"] == []
        assert form.reply["content"].startswith("📣 Your topic is posted in <#3002>.")

    async def test_a_channel_the_bot_cant_post_in_gets_a_message(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        form = await start_topic(world, host)
        topic_channel = world.channels[TOPIC_CHANNEL_ID]
        topic_channel["permission_overwrites"] = [
            {"id": str(BOT_ROLE_ID), "type": 0, "allow": "0", "deny": str(SEND_MESSAGES)}
        ]
        record = await world.click(host, form.reply, "Advertise")
        assert record.private_texts() == ["I don't have permission to do that. Please ask a server admin to check my role."]
        assert labels(form.reply) == ["Edit", "Advertise", "Create a VC"]

    async def test_an_unclicked_card_closes(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topic_ad, "TOPIC_IDLE_MINUTES", 0.005)  # 0.3 seconds.
        host = world.add_member("Alice")
        card = await advertise(world, host)
        await world.wait_for(lambda: not card["components"])
        assert card["embeds"][0]["footer"]["text"] == "This topic has closed. Use /topic to start a new one."


class TestInterest:
    async def test_clicking_adds_you_and_clicking_again_takes_you_off(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host, bob = world.add_member("Alice"), world.add_member("Bob")
        card = await advertise(world, host)
        await world.click(bob, card, "I'm interested")
        assert (interested_count(card), field(card, "Interested")) == ("Interested (1)", f"<@{bob}>")
        await world.click(bob, card, "I'm interested")
        assert (interested_count(card), field(card, "Interested")) == ("Interested (0)", "Nobody yet")

    async def test_everyone_interested_is_listed_in_order(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        people = [world.add_member(name) for name in ("Bob", "Carol", "Dan")]
        card = await advertise(world, host)
        for person in people:
            await world.click(person, card, "I'm interested")
        assert interested_count(card) == "Interested (3)"
        assert field(card, "Interested") == ", ".join(f"<@{person}>" for person in people)

    async def test_the_host_is_told_they_are_already_in(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        card = await advertise(world, host)
        record = await world.click(host, card, "I'm interested")
        assert record.private_texts() == ["You started this topic, so you're already in."]
        assert interested_count(card) == "Interested (0)"


class TestCreateVC:
    async def test_only_the_host_can_create_the_vc(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host, bob = world.add_member("Alice"), world.add_member("Bob")
        card = await advertise(world, host)
        record = await world.click(bob, card, "Create a VC")
        assert record.private_texts() == ["Only the person who started this topic can do this."]
        assert world.temp_channels() == []

    async def test_the_vc_gets_the_chosen_name_and_status_and_the_host_is_moved_in(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        await world.join_voice(host, lounge(world))
        card = await advertise(world, host)
        await world.click(host, card, "Create a VC")
        channel = world.only_temp_channel()
        vc = int(channel["id"])
        assert channel["name"] == NAME
        assert world.voice_statuses[vc] == STATUS
        assert world.voice[host] == vc

    async def test_the_vc_has_the_usual_controls_but_no_naming_prompt(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        await world.join_voice(host, lounge(world))
        card = await advertise(world, host)
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        assert world.panel_message(vc) and world.timer_message(vc)
        assert not any("please name your channel" in m["content"] for m in world.messages_in(vc))
        assert world.nick_of(host) == "👑 Alice"

    async def test_everyone_interested_is_pinged_with_a_link_to_join(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host, bob, carol = world.add_member("Alice"), world.add_member("Bob"), world.add_member("Carol")
        card = await advertise(world, host)
        for person in (bob, carol):
            await world.click(person, card, "I'm interested")
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        [invite] = invites(world)
        assert invite["content"] == f"<@{bob}> <@{carol}> **{NAME}** {OPEN_MESSAGE}. Join here: <#{vc}>"
        # Only the interested people are pinged, not the host.
        assert sorted(int(user) for user in invite["allowed_mentions_sent"]["users"]) == sorted([bob, carol])

    async def test_a_large_crowd_is_pinged_over_several_messages(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topics, "INVITES_PER_MESSAGE", 2)
        host = world.add_member("Alice")
        people = [world.add_member(f"Person {n}") for n in range(5)]
        card = await advertise(world, host)
        for person in people:
            await world.click(person, card, "I'm interested")
        await world.click(host, card, "Create a VC")
        pinged = [int(user) for invite in invites(world) for user in invite["allowed_mentions_sent"]["users"]]
        assert len(invites(world)) == 3
        assert pinged == people

    async def test_a_topic_name_cannot_ping_anybody_else(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host, bob = world.add_member("Alice"), world.add_member("Bob")
        form = await start_topic(world, host, "@everyone", STATUS, TEXT)
        await world.click(host, form.reply, "Advertise")
        card = public_card(world)
        await world.click(bob, card, "I'm interested")
        await world.click(host, card, "Create a VC")
        [invite] = invites(world)
        mentions = invite["allowed_mentions_sent"]
        assert mentions["parse"] == [] and [int(user) for user in mentions["users"]] == [bob]

    async def test_the_card_shows_the_vc_and_its_buttons_are_removed(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        card = await advertise(world, host)
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        assert field(card, "Voice channel") == f"<#{vc}>"
        assert card["components"] == []
        assert card["embeds"][0]["footer"]["text"] == "The VC is open. Click the channel above to join."

    async def test_the_host_gets_a_private_link_to_the_vc(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        await world.join_voice(host, lounge(world))
        card = await advertise(world, host)
        record = await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        assert record.private_texts() == [f"✅ Your VC is ready: <#{vc}>"]

    async def test_the_vc_can_be_created_from_the_private_card_without_advertising(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        await world.join_voice(host, lounge(world))
        card = (await start_topic(world, host)).reply
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        assert world.voice[host] == vc
        assert card["content"] == f"✅ Your VC is ready: <#{vc}>"
        assert card["components"] == []
        assert world.messages_in(TOPIC_CHANNEL_ID) == []

    async def test_a_double_click_creates_only_one_vc(self, world: FakeDiscord, bot: TempVCBot) -> None:
        world.channel_create_delay = 0.1
        host = world.add_member("Alice")
        card = await advertise(world, host)
        first, second = await asyncio.gather(world.click(host, card, "Create a VC"), world.click(host, card, "Create a VC"))
        await world.settle()
        assert world.request_count("POST", f"/guilds/{GUILD_ID}/channels") == 1
        assert len(world.temp_channels()) == 1
        assert "The VC for this topic is already being created." in first.private_texts() + second.private_texts()

    async def test_a_host_who_already_owns_a_vc_is_told_to_end_it_first(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        await world.join_voice(host, HUB_ID)
        await world.settle()
        existing = int(world.only_temp_channel()["id"])
        card = await advertise(world, host)
        record = await world.click(host, card, "Create a VC")
        assert record.private_texts()[-1] == (
            f"You already have a temporary VC: <#{existing}>. End it before creating another one."
        )
        assert len(world.temp_channels()) == 1
        assert labels(card) == ["I'm interested", "Create a VC"]

    async def test_the_timer_starts_when_an_invited_person_joins(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host, bob = world.add_member("Alice"), world.add_member("Bob")
        await world.join_voice(host, lounge(world))
        card = await advertise(world, host)
        await world.click(bob, card, "I'm interested")
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        session = bot.sessions.by_channel(vc)
        assert session is not None and session.phase is TimerPhase.WAITING
        await world.join_voice(bob, vc)
        await world.settle()
        assert session.phase is TimerPhase.RUNNING


class TestHostNotInVoice:
    async def test_the_host_is_warned_that_an_empty_vc_will_be_deleted(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        card = await advertise(world, host)
        record = await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        assert record.private_texts()[-1] == (
            f"✅ Your VC is ready: <#{vc}> If nobody joins within 5 minutes, it will be deleted."
        )
        assert host not in world.voice

    async def test_an_empty_vc_is_deleted_if_nobody_joins_in_time(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topics, "TOPIC_JOIN_DEADLINE_MINUTES", 0.005)  # 0.3 seconds.
        host = world.add_member("Alice")
        card = await advertise(world, host)
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        await world.wait_for(lambda: vc not in world.channels)
        assert bot.sessions.by_channel(vc) is None
        assert world.nick_of(host) is None  # The crown came off too.

    async def test_the_vc_stays_if_someone_joins_in_time(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topics, "TOPIC_JOIN_DEADLINE_MINUTES", 0.005)
        host, bob = world.add_member("Alice"), world.add_member("Bob")
        card = await advertise(world, host)
        await world.click(bob, card, "I'm interested")
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        await world.join_voice(bob, vc)
        await asyncio.sleep(0.5)
        await world.settle()
        assert vc in world.channels
        # The host isn't in it, so Bob counts as someone else and starts the timer.
        session = bot.sessions.by_channel(vc)
        assert session is not None and session.timer_started

    async def test_a_reconnect_does_not_delete_a_vc_still_waiting_for_its_first_person(self, world: FakeDiscord, bot: TempVCBot) -> None:
        host = world.add_member("Alice")
        card = await advertise(world, host)
        await world.click(host, card, "Create a VC")
        vc = int(world.only_temp_channel()["id"])
        await world.force_new_gateway_session()
        assert vc in world.channels


class TestTopicSetup:
    async def test_a_failed_registration_is_logged_and_everything_else_still_works(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        world.refuse_command_registration = True
        with caplog.at_level(logging.ERROR):
            await start_bot()
        assert "Could not register /topic with Discord" in caplog.text
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert len(world.temp_channels()) == 1

    async def test_a_missing_topic_channel_is_logged(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        del world.channels[TOPIC_CHANNEL_ID]
        with caplog.at_level(logging.ERROR):
            await start_bot()
        assert f"TOPIC_CHANNEL_ID {TOPIC_CHANNEL_ID} is not a text channel in this server." in caplog.text

    async def test_missing_permissions_in_the_topic_channel_are_logged(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        world.channels[TOPIC_CHANNEL_ID]["permission_overwrites"] = [
            {"id": str(BOT_ROLE_ID), "type": 0, "allow": "0", "deny": str(SEND_MESSAGES)}
        ]
        with caplog.at_level(logging.ERROR):
            await start_bot()
        # Discord also takes away Embed Links from anyone who can't send messages.
        assert "The bot is missing these permissions in the topic channel: Send Messages, Embed Links." in caplog.text


class TestChannelTopic:
    async def test_the_channels_topic_explains_topic(self, world: FakeDiscord, bot: TempVCBot) -> None:
        topic = world.channels[TOPIC_CHANNEL_ID]["topic"]
        assert topic == TOPIC_CHANNEL_DESCRIPTION
        assert "Type **/topic** to start a topic" in topic
        assert topic.endswith("Please don't write anything else in this channel.")

    async def test_nothing_is_posted_in_the_chat_for_everyone(self, world: FakeDiscord, bot: TempVCBot) -> None:
        assert world.messages_in(TOPIC_CHANNEL_ID) == []

    async def test_a_topic_that_is_already_right_is_not_written_again(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        world.channels[TOPIC_CHANNEL_ID]["topic"] = TOPIC_CHANNEL_DESCRIPTION
        await start_bot()
        await world.force_new_gateway_session()
        assert world.request_count("PATCH", f"/channels/{TOPIC_CHANNEL_ID}") == 0

    async def test_a_different_topic_is_replaced(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        world.channels[TOPIC_CHANNEL_ID]["topic"] = "Old description"
        await start_bot()
        assert world.channels[TOPIC_CHANNEL_ID]["topic"] == TOPIC_CHANNEL_DESCRIPTION

    async def test_a_bot_that_cant_edit_the_channel_says_so(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        world.channels[TOPIC_CHANNEL_ID]["permission_overwrites"] = [
            {"id": str(BOT_ROLE_ID), "type": 0, "allow": "0", "deny": str(MANAGE_CHANNELS)}
        ]
        with caplog.at_level(logging.WARNING):
            await start_bot()
        assert "The bot is missing these permissions in the topic channel: Manage Channels." in caplog.text
        assert "Could not set the topic channel's topic" in caplog.text
        assert world.channels[TOPIC_CHANNEL_ID]["topic"] is None

    def test_the_text_fits_discords_limit(self) -> None:
        assert len(TOPIC_CHANNEL_DESCRIPTION) <= 1024


class TestTopicUnits:
    def test_interest_toggles_and_keeps_the_order_people_clicked_in(self) -> None:
        topic = Topic(host_id=1, name=NAME, status=STATUS, text=TEXT)
        assert topic.toggle_interest(3) and topic.toggle_interest(2)
        assert not topic.toggle_interest(3)
        assert topic.toggle_interest(3)
        assert list(topic.interested_ids) == [2, 3]

    def test_a_long_interested_list_is_cut_short(self) -> None:
        topic = Topic(host_id=1, name=NAME, status=STATUS, text=TEXT)
        for member_id in range(100, 100 + MAX_INTERESTED_SHOWN + 5):
            topic.toggle_interest(member_id)
        text = interested_list(topic)
        assert text.endswith(" and 5 more")
        assert text.count("<@") == MAX_INTERESTED_SHOWN
        assert len(text) <= 1024  # Discord's limit for an embed field.
