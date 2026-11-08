"""Run the real bot against the fake Discord, one requirement from the brief at a time."""

from __future__ import annotations

import io
import logging
import re
import time
from typing import Any

import discord
import pytest
from PIL import Image

from temp_vc_bot import constants, countdown_ring
from temp_vc_bot.bot import TempVCBot
from temp_vc_bot.services import channel_manager, timer
from temp_vc_bot.state import TimerPhase
from temp_vc_bot.views import extend_prompt

from .conftest import BotStarter
from .fake_discord import (
    ATTACH_FILES,
    BOT_USER_ID,
    CATEGORY_ID,
    CONNECT,
    EVERYONE_ROLE_ID,
    GUILD_ID,
    HUB_ID,
    INVITE_PERMISSIONS,
    MANAGE_CHANNELS,
    MANAGE_ROLES,
    MOVE_MEMBERS,
    VIEW_CHANNEL,
    FakeDiscord,
    find_component,
)

NOT_OWNER = "Only the channel owner can do this."


async def make_vc(world: FakeDiscord, name: str = "Alice") -> tuple[int, int]:
    """A member joins the hub. Returns their ID and the ID of their new channel."""
    existing = {int(c["id"]) for c in world.temp_channels()}
    owner = world.add_member(name)
    await world.join_voice(owner, HUB_ID)
    await world.settle()
    new = [int(c["id"]) for c in world.temp_channels() if int(c["id"]) not in existing]
    assert len(new) == 1, "Joining the hub should create exactly one channel."
    return owner, new[0]


def timer_text(world: FakeDiscord, channel_id: int) -> str:
    return world.timer_message(channel_id)["embeds"][0]["description"]


def shown_timestamp(world: FakeDiscord, channel_id: int) -> int:
    """The Unix time in the timer embed's <t:...:T> closing time."""
    match = re.search(r"<t:(\d+):T>", timer_text(world, channel_id))
    assert match, f"No relative timestamp in: {timer_text(world, channel_id)}"
    return int(match.group(1))


def lock_label(world: FakeDiscord, channel_id: int) -> str:
    panel = world.panel_message(channel_id)
    labels = [button["label"] for button in _buttons(panel)]
    return next(label for label in labels if "ocked" in label)


def _buttons(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [component for row in message["components"] for component in row["components"] if component["type"] == 2]


def dropdown(world: FakeDiscord, channel_id: int) -> dict[str, Any]:
    return find_component(world.panel_message(channel_id), "type", 3)


class TestCreatingTheVC:
    async def test_joining_the_hub_creates_a_channel_and_moves_the_owner_in(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world, "Alice")
        channel = world.channels[channel_id]
        assert channel["name"] == "Alice's VC"
        assert channel["parent_id"] == str(CATEGORY_ID)
        assert world.voice[owner] == channel_id

    async def test_owner_rejoining_the_hub_goes_back_to_their_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.voice[owner] == channel_id
        assert len(world.temp_channels()) == 1

    async def test_owner_alone_in_their_channel_clicking_the_hub_keeps_the_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Leaving makes the channel look empty for a moment, which must not delete it.
        owner, channel_id = await make_vc(world)
        world.requests.clear()
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert channel_id in world.channels
        assert world.voice[owner] == channel_id
        assert world.request_count("POST", "/channels") == 0

    async def test_owner_outside_voice_rejoins_a_channel_friends_are_still_in(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.leave_voice(owner)
        await world.settle()
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.voice[owner] == channel_id
        assert len(world.temp_channels()) == 1

    async def test_rejoining_the_hub_while_a_channel_is_being_created_makes_only_one(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Creating a channel takes a moment, and a second join can arrive during it.
        # The events are spaced out, because discord.py reuses the same VoiceState object,
        # so a handler for a burst of events only ever sees the latest state.
        world.channel_create_delay = 0.1
        create_channel = f"/guilds/{GUILD_ID}/channels"
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.wait_for(lambda: world.request_count("POST", create_channel) == 1)
        await world.leave_voice(owner)
        await world.join_voice(owner, HUB_ID)
        await world.wait_for(lambda: world.voice[owner] != HUB_ID)
        await world.settle()
        # Checking the end result isn't enough: a second channel can appear, and then
        # get cleaned up for being empty, which people would see as a flicker.
        assert world.request_count("POST", create_channel) == 1
        assert world.voice[owner] == int(world.only_temp_channel()["id"])

    async def test_two_owners_get_separate_channels(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, first = await make_vc(world, "Alice")
        _, second = await make_vc(world, "Bob")
        assert first != second
        assert len(world.temp_channels()) == 2

    async def test_muting_while_in_the_hub_does_not_create_a_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # The member stays in the hub, because the move fails.
        owner = world.add_member("Alice")
        world.refuse_to_move.add(owner)
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        world.requests.clear()
        await world.join_voice(owner, HUB_ID)  # Same channel: only mute or deafen changed.
        await world.settle()
        assert world.requests == []

    async def test_a_bot_joining_the_hub_gets_no_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        music_bot = world.add_member("MusicBot", bot=True)
        await world.join_voice(music_bot, HUB_ID)
        await world.settle()
        assert world.temp_channels() == []

    async def test_member_leaving_before_the_move_leaves_no_empty_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Nobody ever entered this channel, so no "leave" event could clean it up.
        owner = world.add_member("Alice")
        world.refuse_to_move.add(owner)
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.temp_channels() == []
        assert bot.sessions.by_owner(owner) is None

    async def test_new_channel_copies_a_private_category(self, world: FakeDiscord, bot: TempVCBot) -> None:
        category = world.channels[CATEGORY_ID]
        private = {"id": str(EVERYONE_ROLE_ID), "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}
        category["permission_overwrites"] = [private]
        await world.force_new_gateway_session()
        await world.settle()
        _, channel_id = await make_vc(world)
        assert world.overwrite_on(channel_id, EVERYONE_ROLE_ID)["deny"] == str(VIEW_CHANNEL)


class TestBotsRole:
    async def test_bot_overwrite_grants_what_discord_allows_and_it_still_manages_the_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        overwrite = world.overwrite_on(channel_id, BOT_USER_ID)
        allowed = int(overwrite["allow"])
        for permission in (VIEW_CHANNEL, CONNECT, MANAGE_CHANNELS, MOVE_MEMBERS):
            assert allowed & permission, "The overwrite should grant this."
        # Discord refuses this bit from a non-admin bot. The bot has it from its role.
        assert not allowed & MANAGE_ROLES
        effective = world.effective_permissions(channel_id, BOT_USER_ID)
        assert effective & MANAGE_ROLES, "Manage Permissions should still be effective."

    async def test_administrator_bot_also_writes_manage_permissions_into_the_overwrite(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        world.bot_permissions = 1 << 3  # Administrator.
        await start_bot()
        _, channel_id = await make_vc(world)
        assert int(world.overwrite_on(channel_id, BOT_USER_ID)["allow"]) & MANAGE_ROLES

    async def test_bot_joins_deafened_and_muted(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        assert world.voice[BOT_USER_ID] == channel_id
        assert world.bot_voice_flags == {"self_deaf": True, "self_mute": True}

    async def test_bot_already_in_a_channel_stays_there_and_manages_the_new_one(self, world: FakeDiscord, bot: TempVCBot, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        _, first = await make_vc(world, "Alice")
        second_owner, second = await make_vc(world, "Bob")
        assert world.voice[BOT_USER_ID] == first
        assert BOT_USER_ID not in world.people_in(second)
        assert "by permissions only" in caplog.text, "This is expected, so it shouldn't show as a failed connection."
        assert "Could not join voice channel" not in caplog.text
        # The bot can still manage the second channel, using its permissions.
        record = await world.click(second_owner, world.panel_message(second), "Unlocked (click to lock)")
        assert record.acknowledged
        assert int(world.overwrite_on(second, EVERYONE_ROLE_ID)["deny"]) & CONNECT

    async def test_bot_can_join_a_later_channel_after_its_first_one_closes(self, world: FakeDiscord, bot: TempVCBot) -> None:
        first_owner, first = await make_vc(world, "Alice")
        await world.leave_voice(first_owner)
        await world.settle()
        assert first not in world.channels
        assert BOT_USER_ID not in world.voice
        _, second = await make_vc(world, "Bob")
        assert world.voice[BOT_USER_ID] == second


class TestControlPanelAccess:
    async def test_panel_really_pings_the_owner_so_they_notice_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Writing a mention isn't enough: the bot blocks all pings unless a message allows them.
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        assert f"<@{owner}>" in panel["content"]
        assert [int(user) for user in panel["allowed_mentions_sent"]["users"]] == [owner]

    async def test_panel_is_posted_in_the_channels_own_text_chat(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        assert world.panel_message(channel_id)["channel_id"] == str(channel_id)
        assert world.panel_message(channel_id)["embeds"]

    @pytest.mark.parametrize("label", ["Rename", "User limit", "Unlocked (click to lock)"])
    async def test_non_owner_clicking_a_button_gets_only_a_private_reply(self, world: FakeDiscord, bot: TempVCBot, label: str) -> None:
        _, channel_id = await make_vc(world)
        stranger = world.add_member("Mallory")
        record = await world.click(stranger, world.panel_message(channel_id), label)
        assert record.private_texts() == [NOT_OWNER]
        assert record.public_texts() == []
        assert int((world.overwrite_on(channel_id, EVERYONE_ROLE_ID) or {"deny": 0})["deny"]) == 0

    async def test_non_owner_using_the_dropdown_changes_nothing(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        stranger = world.add_member("Mallory")
        record = await world.choose(stranger, world.panel_message(channel_id), "40")
        assert record.private_texts() == [NOT_OWNER]
        assert bot.sessions.by_owner(owner).timer_minutes == 20

    async def test_panel_buttons_outlive_the_default_view_timeout(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, _ = await make_vc(world)
        assert bot.sessions.by_owner(owner).panel_view.timeout is None


class TestRename:
    async def test_rename(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        assert click.modal["components"]
        result = await world.submit_modal(owner, panel, click.modal, "Movie night")
        assert world.channels[channel_id]["name"] == "Movie night"
        assert "Movie night" in result.private_texts()[0]

    @pytest.mark.parametrize(("button", "value"), [("Rename", "Movie night"), ("User limit", "5")])
    async def test_owner_sees_a_private_thinking_state_while_a_form_is_processed(self, world: FakeDiscord, bot: TempVCBot, button: str, value: str) -> None:
        # Otherwise nothing appears after Submit, and a short rate limit can take 30 seconds.
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, button)
        result = await world.submit_modal(owner, panel, click.modal, value)
        first_response = result.responses[0]
        assert first_response["type"] == 5, "Type 5 is the 'thinking' state."
        assert first_response["data"]["flags"] & 64, "It must be private."
        assert result.public_texts() == []

    async def test_form_enforces_1_to_100_characters(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        click = await world.click(owner, world.panel_message(channel_id), "Rename")
        text_box = find_component(click.modal, "type", 4)
        assert (text_box["min_length"], text_box["max_length"]) == (1, 100)

    async def test_100_characters_are_accepted(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        await world.submit_modal(owner, panel, click.modal, "x" * 100)
        assert world.channels[channel_id]["name"] == "x" * 100

    async def test_name_of_only_spaces_is_rejected_with_a_message(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        result = await world.submit_modal(owner, panel, click.modal, "   ")
        assert world.channels[channel_id]["name"] == "Alice's VC"
        assert "1–100" in result.private_texts()[0]

    async def test_name_discord_refuses_gets_a_message(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        result = await world.submit_modal(owner, panel, click.modal, "banned-word")
        assert "rejected" in result.private_texts()[0]

    @pytest.mark.parametrize("exhausted_bucket", [False, True])
    async def test_third_rename_says_try_later_quickly_and_does_not_hang(self, world: FakeDiscord, bot: TempVCBot, exhausted_bucket: bool) -> None:
        world.report_exhausted_rename_bucket = exhausted_bucket
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        started = time.monotonic()
        for name in ("First", "Second"):
            click = await world.click(owner, panel, "Rename")
            await world.submit_modal(owner, panel, click.modal, name)
        click = await world.click(owner, panel, "Rename")
        result = await world.submit_modal(owner, panel, click.modal, "Third")

        assert world.channels[channel_id]["name"] == "Second"
        assert result.private_texts() == ["Discord only allows about 2 renames per 10 minutes for each channel. Please try again in about 9 minutes."]
        assert time.monotonic() - started < 10, "A rate limit must not make the rename hang."

    async def test_a_name_like_at_everyone_cannot_ping_anybody(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        result = await world.submit_modal(owner, panel, click.modal, "@everyone")
        assert "@everyone" in result.private_texts()[0]
        assert result.followups[-1]["allowed_mentions"]["parse"] == [], "The reply must not be able to ping anyone."

    async def test_renaming_to_the_same_name_does_not_use_up_a_rename(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        await world.submit_modal(owner, panel, click.modal, "Alice's VC")
        assert world.renames[channel_id] == []


class TestUserLimit:
    async def set_limit(self, world: FakeDiscord, owner: int, channel_id: int, value: str) -> Any:
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "User limit")
        return await world.submit_modal(owner, panel, click.modal, value)

    async def test_limit_gets_an_extra_seat_when_the_bot_is_in_the_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        result = await self.set_limit(world, owner, channel_id, "2")
        assert world.channels[channel_id]["user_limit"] == 3
        assert "set to 2" in result.private_texts()[0]

    async def test_limit_really_admits_that_many_humans(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await self.set_limit(world, owner, channel_id, "2")
        second, third = world.add_member("Bob"), world.add_member("Carol")
        assert world.can_join(second, channel_id)
        await world.join_voice(second, channel_id)
        await world.settle()
        assert not world.can_join(third, channel_id), "Two humans plus the bot fill the channel."

    async def test_no_extra_seat_when_the_bot_is_elsewhere(self, world: FakeDiscord, bot: TempVCBot) -> None:
        await make_vc(world, "Alice")  # The bot sits in Alice's channel.
        owner, channel_id = await make_vc(world, "Bob")
        await self.set_limit(world, owner, channel_id, "2")
        assert world.channels[channel_id]["user_limit"] == 2

    async def test_zero_means_no_limit_even_with_the_bot_inside(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await self.set_limit(world, owner, channel_id, "5")
        result = await self.set_limit(world, owner, channel_id, "0")
        assert world.channels[channel_id]["user_limit"] == 0
        assert "removed" in result.private_texts()[0]

    async def test_99_with_the_bot_inside_stays_within_discords_maximum_and_says_so(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        result = await self.set_limit(world, owner, channel_id, "99")
        assert world.channels[channel_id]["user_limit"] == 99
        assert "only 98 people fit" in result.private_texts()[0]

    @pytest.mark.parametrize("value", ["abc", "100", "-1"])
    async def test_invalid_numbers_get_a_message_and_change_nothing(self, world: FakeDiscord, bot: TempVCBot, value: str) -> None:
        owner, channel_id = await make_vc(world)
        result = await self.set_limit(world, owner, channel_id, value)
        assert world.channels[channel_id]["user_limit"] == 0
        assert "whole number" in result.private_texts()[0]

    async def test_seat_is_removed_when_the_bot_leaves(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await self.set_limit(world, owner, channel_id, "4")
        assert world.channels[channel_id]["user_limit"] == 5
        await bot.channel_manager.guild.voice_client.disconnect(force=True)
        await world.settle()
        assert world.channels[channel_id]["user_limit"] == 4

    async def test_seat_is_added_for_another_bot_too(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await self.set_limit(world, owner, channel_id, "4")
        music_bot = world.add_member("MusicBot", bot=True)
        await world.join_voice(music_bot, channel_id)
        await world.settle()
        assert world.channels[channel_id]["user_limit"] == 6

    async def test_panel_shows_the_human_number_not_the_seat_count(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await self.set_limit(world, owner, channel_id, "4")
        fields = world.panel_message(channel_id)["embeds"][0]["fields"]
        assert next(f["value"] for f in fields if f["name"] == "User limit") == "4"


class TestLock:
    async def test_label_shows_the_current_state(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        assert lock_label(world, channel_id).startswith("Unlocked")
        await world.click(owner, world.panel_message(channel_id), "Unlocked (click to lock)")
        assert lock_label(world, channel_id).startswith("Locked")
        await world.click(owner, world.panel_message(channel_id), "Locked (click to unlock)")
        assert lock_label(world, channel_id).startswith("Unlocked")

    async def test_locking_blocks_everyone_except_the_owner_and_bot(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.click(owner, world.panel_message(channel_id), "Unlocked (click to lock)")
        stranger = world.add_member("Mallory")
        assert not world.can_join(stranger, channel_id)
        assert world.can_join(owner, channel_id)
        assert world.can_join(BOT_USER_ID, channel_id)

    async def test_people_inside_stay_when_the_channel_locks(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.settle()
        await world.click(owner, world.panel_message(channel_id), "Unlocked (click to lock)")
        assert world.voice[friend] == channel_id

    async def test_unlocking_restores_normal_access_instead_of_forcing_allow(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        await world.click(owner, panel, "Unlocked (click to lock)")
        await world.click(owner, world.panel_message(channel_id), "Locked (click to unlock)")
        assert world.can_join(world.add_member("Mallory"), channel_id)
        overwrite = world.overwrite_on(channel_id, EVERYONE_ROLE_ID)
        assert overwrite is None or not int(overwrite["allow"]) & CONNECT, "Unlock must not force-allow Connect."

    async def test_unlocking_keeps_a_private_categorys_other_rules(self, world: FakeDiscord, bot: TempVCBot) -> None:
        category = world.channels[CATEGORY_ID]
        category["permission_overwrites"] = [{"id": str(EVERYONE_ROLE_ID), "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}]
        await world.force_new_gateway_session()
        await world.settle()
        owner, channel_id = await make_vc(world)
        await world.click(owner, world.panel_message(channel_id), "Unlocked (click to lock)")
        await world.click(owner, world.panel_message(channel_id), "Locked (click to unlock)")
        assert int(world.overwrite_on(channel_id, EVERYONE_ROLE_ID)["deny"]) == VIEW_CHANNEL

    async def test_bot_can_still_manage_a_locked_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # On Discord, losing Connect also takes away Manage Channels. The bot's own
        # overwrite is what prevents that.
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        await world.click(owner, panel, "Unlocked (click to lock)")
        click = await world.click(owner, world.panel_message(channel_id), "Rename")
        await world.submit_modal(owner, panel, click.modal, "Locked room")
        assert world.channels[channel_id]["name"] == "Locked room"
        await world.leave_voice(owner)
        await world.settle()
        assert channel_id not in world.channels, "A locked channel must still be deletable."


class TestSetTimer:
    async def test_dropdown_offers_the_choices_with_20_selected(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        options = dropdown(world, channel_id)["options"]
        assert [o["value"] for o in options] == ["20", "25", "30", "35", "40"]
        assert [o["value"] for o in options if o.get("default")] == ["20"]
        assert dropdown(world, channel_id).get("disabled") is False

    async def test_choosing_a_length_is_used_when_the_timer_starts(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.choose(owner, world.panel_message(channel_id), "35")
        session = bot.sessions.by_owner(owner)
        assert session.timer_minutes == 35
        options = dropdown(world, channel_id)["options"]
        assert [o["value"] for o in options if o.get("default")] == ["35"]
        before = discord.utils.utcnow()
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        minutes = (session.ends_at - before).total_seconds() / 60
        assert 34.9 < minutes <= 35.1

    async def test_dropdown_is_disabled_once_the_timer_is_running(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        assert dropdown(world, channel_id)["disabled"] is True

    async def test_a_screen_that_is_behind_still_cant_change_a_running_timer(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        stale_panel = dict(world.panel_message(channel_id))
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        session = bot.sessions.by_owner(owner)
        ends_at = session.ends_at
        record = await world.choose(owner, stale_panel, "40")
        assert session.timer_minutes == 20
        assert session.ends_at == ends_at
        assert "already started" in record.private_texts()[0]


class TestTimerStart:
    async def test_shows_waiting_until_someone_else_joins(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        assert timer_text(world, channel_id) == "Waiting for someone to join…"
        assert bot.sessions.by_owner(owner).phase is TimerPhase.WAITING

    async def test_our_bot_being_in_the_channel_does_not_start_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        assert BOT_USER_ID in world.people_in(channel_id)
        assert not bot.sessions.by_owner(owner).timer_started

    async def test_another_bot_joining_does_not_start_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("MusicBot", bot=True), channel_id)
        await world.settle()
        assert not bot.sessions.by_owner(owner).timer_started

    async def test_owner_rejoining_does_not_start_it(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert not bot.sessions.by_owner(owner).timer_started

    async def test_second_human_starts_it_and_shows_the_closing_time(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        session = bot.sessions.by_owner(owner)
        assert session.phase is TimerPhase.RUNNING
        assert shown_timestamp(world, channel_id) == int(session.ends_at.timestamp())

    async def test_keeps_running_when_people_leave(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.settle()
        session = bot.sessions.by_owner(owner)
        ends_at = session.ends_at
        await world.leave_voice(friend)
        await world.settle()
        assert session.phase is TimerPhase.RUNNING
        assert session.ends_at == ends_at

    async def test_owner_leaving_does_not_stop_it_while_others_remain(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.settle()
        await world.leave_voice(owner)
        await world.settle()
        assert channel_id in world.channels
        assert bot.sessions.by_owner(owner).phase is TimerPhase.RUNNING


@pytest.fixture
def fast_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make one "minute" last 0.2 seconds, so timers finish during a test.

    This also turns off the ring's scheduled redraws. At this speed they would
    come every 0.2 seconds and race with the tests' clicks and edit counts.
    TestCountdownRing turns them back on where it needs them.
    """
    monkeypatch.setattr(constants, "SECONDS_PER_MINUTE", 0.2)
    monkeypatch.setattr(timer, "RING_REDRAW_MINUTES", 1000)


@pytest.fixture
def time_to_answer(fast_time: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the answer window open for 4 seconds, so a click lands in time on a slow machine."""
    monkeypatch.setattr(extend_prompt, "ANSWER_WINDOW_MINUTES", 20)


async def run_to_prompt(world: FakeDiscord, bot: TempVCBot) -> tuple[int, int, dict[str, Any]]:
    """Create a channel with a very short timer and wait for the extension prompt."""
    owner, channel_id = await make_vc(world)
    bot.sessions.by_owner(owner).timer_minutes = 1  # 0.2 seconds at the fast_time speed.
    await world.join_voice(world.add_member("Bob"), channel_id)
    await world.wait_for(lambda: world.prompt_message(channel_id) is not None)
    # discord.py starts listening to a message's buttons once its send call returns, which
    # is a moment after the message exists. A person can't click that fast, but a test can.
    await world.settle()
    return owner, channel_id, world.prompt_message(channel_id)


@pytest.mark.usefixtures("fast_time")
class TestNoAnswer:
    """These wait for the real answer window to run out."""

    async def test_no_answer_within_the_window_counts_as_no(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, _ = await run_to_prompt(world, bot)
        task = bot.sessions.by_owner(owner).timer_task
        await world.wait_for(lambda: channel_id not in world.channels)
        await world.wait_for(task.done)
        assert bot.sessions.by_owner(owner) is None
        assert not task.cancelled(), "The timer must finish its work, not cancel itself partway through closing."

    async def test_strangers_clicking_cant_keep_the_prompt_open(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id, prompt = await run_to_prompt(world, bot)
        window = constants.minutes_to_delta(constants.ANSWER_WINDOW_MINUTES).total_seconds()
        mallory = world.add_member("Mallory")
        started = time.monotonic()
        while channel_id in world.channels:
            # Clicks arrive faster than the window is long. If they reset it, this would never end.
            await world.click(mallory, prompt, "Yes")
            assert time.monotonic() - started < window * 5, "Strangers' clicks kept the prompt open."


@pytest.mark.usefixtures("time_to_answer")
class TestExtensionPrompt:
    async def test_prompt_tags_the_owner_and_uses_the_exact_wording(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        assert prompt["content"] == f"<@{owner}> Time's up! Extend by 10 minutes?"
        assert [int(user) for user in prompt["allowed_mentions_sent"]["users"]] == [owner]

    async def test_non_owner_cant_answer(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        record = await world.click(world.add_member("Mallory"), prompt, "Yes")
        assert record.private_texts() == [NOT_OWNER]
        assert bot.sessions.by_owner(owner).phase is TimerPhase.AWAITING_ANSWER

    async def test_no_closes_the_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        await world.click(owner, prompt, "No")
        await world.wait_for(lambda: channel_id not in world.channels)
        assert bot.sessions.by_owner(owner) is None

    async def test_yes_adds_10_minutes_to_the_original_end_time(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        session = bot.sessions.by_owner(owner)
        base_end = session.ends_at
        await world.click(owner, prompt, "Yes")
        assert session.phase is TimerPhase.EXTENDED
        assert session.ends_at == base_end + constants.minutes_to_delta(10)
        assert shown_timestamp(world, channel_id) == int(session.ends_at.timestamp())
        assert channel_id in world.channels

    async def test_extra_time_ending_closes_the_channel_without_asking_again(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        await world.click(owner, prompt, "Yes")
        prompts_sent = world.request_count("POST", f"/channels/{channel_id}/messages")
        await world.wait_for(lambda: channel_id not in world.channels)
        assert world.request_count("POST", f"/channels/{channel_id}/messages") == prompts_sent, "It must not ask twice."

    async def test_answering_removes_the_buttons(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        await world.click(owner, prompt, "Yes")
        assert world.messages[int(prompt["id"])]["components"] == []

    async def test_timer_embed_is_redrawn_at_every_phase_change(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Scheduled ring redraws are off here (see fast_time), so only phase changes edit it.
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        timer_id = int(world.timer_message(channel_id)["id"])
        await world.click(owner, prompt, "Yes")
        await world.wait_for(lambda: channel_id not in world.channels)
        # Started, time's up, extended, ending.
        assert world.edits_of(timer_id) == 4


Ring = tuple[float, tuple[int, int, int], str, str]


@pytest.fixture
def drawn_rings(monkeypatch: pytest.MonkeyPatch) -> list[Ring]:
    """Record every ring the bot draws, as (fraction left, colour, big text, small text)."""
    drawn: list[Ring] = []
    real_render = countdown_ring.render_ring

    def record(fraction_left: float, colour: tuple[int, int, int], headline: str, caption: str) -> bytes:
        drawn.append((fraction_left, colour, headline, caption))
        return real_render(fraction_left, colour, headline, caption)

    monkeypatch.setattr(countdown_ring, "render_ring", record)
    return drawn


class TestCountdownRing:
    async def test_timer_message_shows_the_ring_image(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        message = world.timer_message(channel_id)
        assert message["embeds"][0]["image"]["url"] == "attachment://countdown.png"
        assert [attachment["filename"] for attachment in message["attachments"]] == ["countdown.png"]
        with Image.open(io.BytesIO(world.timer_ring(channel_id))) as image:
            assert image.format == "PNG"

    async def test_ring_is_full_and_grey_while_waiting(self, world: FakeDiscord, bot: TempVCBot, drawn_rings: list[Ring]) -> None:
        await make_vc(world)
        assert drawn_rings == [(1.0, discord.Colour.light_grey().to_rgb(), "Waiting", "for someone to join")]

    async def test_a_new_ring_replaces_the_old_one(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        waiting_ring = world.timer_ring(channel_id)
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        # timer_ring() also checks there's exactly one image, so old rings don't pile up.
        assert world.timer_ring(channel_id) != waiting_ring

    @pytest.mark.usefixtures("time_to_answer")
    async def test_nothing_in_the_timer_message_changes_on_its_own(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # A live <t:...:R> countdown changes every second inside Discord, which made the
        # ring flicker. Every phase must show only fixed text, which only the bot changes.
        owner, channel_id, prompt = await run_to_prompt(world, bot)
        texts = [timer_text(world, channel_id)]
        await world.click(owner, prompt, "Yes")
        texts.append(timer_text(world, channel_id))
        assert all(":R>" not in text for text in texts), texts

    @pytest.mark.usefixtures("fast_time")
    async def test_ring_counts_down_on_schedule_between_phase_changes(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch, drawn_rings: list[Ring]) -> None:
        monkeypatch.setattr(timer, "RING_REDRAW_MINUTES", 1)  # Every 0.2 seconds at this speed.
        owner, channel_id = await make_vc(world)
        bot.sessions.by_owner(owner).timer_minutes = 5
        timer_id = int(world.timer_message(channel_id)["id"])
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.wait_for(lambda: world.prompt_message(channel_id) is not None)

        green, orange = discord.Colour.green().to_rgb(), discord.Colour.orange().to_rgb()
        running = drawn_rings[1:6]
        assert [ring[2:] for ring in running] == [(str(n), "min left") for n in (5, 4, 3, 2, 1)]
        assert [ring[1] for ring in running] == [green] * 5
        # The ring empties a fifth at a time. Redraws are drawn as of their planned
        # moment, so those four are exact. The first one is a moment after the start.
        assert running[0][0] == pytest.approx(1.0, abs=0.05)
        assert [ring[0] for ring in running[1:]] == [0.8, 0.6, 0.4, 0.2]
        # Then a fresh, full orange ring for the owner's answer window.
        assert drawn_rings[6][1:] == (orange, "2", "min to answer")
        assert drawn_rings[6][0] == pytest.approx(1.0, abs=0.05)
        # Started, four redraws, time's up.
        assert world.edits_of(timer_id) == 6

    @pytest.mark.usefixtures("fast_time")
    async def test_a_ring_that_fails_to_draw_leaves_a_working_text_timer(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        def broken(*args: Any) -> bytes:
            raise ValueError("Drawing failed.")

        monkeypatch.setattr(countdown_ring, "render_ring", broken)
        owner, channel_id = await make_vc(world)
        message = world.timer_message(channel_id)
        assert "image" not in message["embeds"][0]
        assert message["attachments"] == []
        assert timer_text(world, channel_id) == "Waiting for someone to join…"
        assert "Could not draw the countdown ring" in caplog.text

        # The countdown still runs, and the channel still closes when nobody answers.
        bot.sessions.by_owner(owner).timer_minutes = 1
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.wait_for(lambda: channel_id not in world.channels)


class TestCleanup:
    async def test_channel_is_deleted_when_the_last_human_leaves(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.leave_voice(owner)
        await world.settle()
        assert channel_id not in world.channels
        assert bot.sessions.by_channel(channel_id) is None

    async def test_bot_leaves_voice_when_its_channel_is_deleted(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, _ = await make_vc(world)
        await world.leave_voice(owner)
        await world.settle()
        assert BOT_USER_ID not in world.voice
        assert bot.channel_manager.guild.voice_client is None
        assert world.bot_left_voice_itself, "The bot should leave before deleting, so it isn't kicked mid-connection."

    async def test_another_bot_left_alone_does_not_keep_the_channel_open(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("MusicBot", bot=True), channel_id)
        await world.settle()
        await world.leave_voice(owner)
        await world.settle()
        assert channel_id not in world.channels

    async def test_channel_stays_while_a_human_remains(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.settle()
        await world.leave_voice(friend)
        await world.settle()
        assert channel_id in world.channels

    async def test_emptying_a_channel_cancels_its_running_timer(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        friend = world.add_member("Bob")
        await world.join_voice(friend, channel_id)
        await world.settle()
        task = bot.sessions.by_owner(owner).timer_task
        await world.leave_voice(friend)
        await world.leave_voice(owner)
        await world.settle()
        assert channel_id not in world.channels
        assert task.done()

    async def test_moving_to_another_temp_vc_counts_as_leaving(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, first = await make_vc(world, "Alice")
        _, second = await make_vc(world, "Bob")
        await world.join_voice(owner, second)
        await world.settle()
        assert first not in world.channels
        assert second in world.channels

    async def test_non_owner_going_to_the_hub_empties_the_old_channel_and_gets_their_own(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, first = await make_vc(world, "Alice")
        friend = world.add_member("Bob")
        await world.join_voice(friend, first)
        await world.settle()
        await world.leave_voice(owner)
        await world.settle()
        await world.join_voice(friend, HUB_ID)
        await world.settle()
        assert first not in world.channels
        assert world.voice[friend] == int(world.only_temp_channel()["id"])

    @pytest.mark.parametrize("delete_event_first", [True, False])
    async def test_deleting_a_channel_by_hand_cancels_its_timer_and_frees_the_owner(self, world: FakeDiscord, bot: TempVCBot, delete_event_first: bool) -> None:
        owner, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        task = bot.sessions.by_owner(owner).timer_task
        await world.delete_channel_by_hand(channel_id, delete_event_first=delete_event_first)
        await world.settle()
        assert task.done()
        assert bot.sessions.by_owner(owner) is None
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert len(world.temp_channels()) == 1, "The owner can get a new channel."

    async def test_a_channel_delete_event_alone_is_enough_to_clean_up(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Normally voice events follow a delete, but the session must not depend on them.
        owner, channel_id = await make_vc(world)
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        task = bot.sessions.by_owner(owner).timer_task
        await world._dispatch("CHANNEL_DELETE", world.channels.pop(channel_id))
        await world.settle()
        assert bot.sessions.by_owner(owner) is None
        assert task.done()

    @pytest.mark.usefixtures("time_to_answer")
    async def test_emptying_a_channel_with_the_prompt_open_closes_it_cleanly(self, world: FakeDiscord, bot: TempVCBot, caplog: pytest.LogCaptureFixture) -> None:
        owner, channel_id, _ = await run_to_prompt(world, bot)
        task = bot.sessions.by_owner(owner).timer_task
        for person in world.people_in(channel_id):
            if person != BOT_USER_ID:
                await world.leave_voice(person)
        await world.settle()
        assert channel_id not in world.channels
        assert task.done()
        assert "Traceback" not in caplog.text


class TestStartupAndReconnect:
    def add_leftover(self, world: FakeDiscord) -> int:
        """A temp VC from before a restart: it carries the bot's own overwrite."""
        bot_mark = {"id": str(BOT_USER_ID), "type": 1, "allow": str(MANAGE_CHANNELS | CONNECT), "deny": "0"}
        return world.add_channel("Old VC", parent_id=CATEGORY_ID, overwrites=[bot_mark])

    async def test_leftovers_are_deleted_on_startup(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        leftover = self.add_leftover(world)
        await start_bot()
        assert leftover not in world.channels

    async def test_hub_in_the_same_category_survives_startup(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        # The bot's overwrite on the hub makes it look like a leftover, except that it's the hub.
        world.channels[HUB_ID]["parent_id"] = str(CATEGORY_ID)
        world.channels[HUB_ID]["permission_overwrites"] = [
            {"id": str(BOT_USER_ID), "type": 1, "allow": str(MANAGE_CHANNELS | CONNECT), "deny": "0"}
        ]
        await start_bot()
        assert HUB_ID in world.channels

    async def test_permanent_channels_in_the_category_survive_startup(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        permanent = world.add_channel("Permanent lounge", parent_id=CATEGORY_ID)
        await start_bot()
        assert permanent in world.channels

    async def test_channels_outside_the_category_survive_startup(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        bot_mark = {"id": str(BOT_USER_ID), "type": 1, "allow": str(MANAGE_CHANNELS | CONNECT), "deny": "0"}
        elsewhere = world.add_channel("Elsewhere", overwrites=[bot_mark])
        await start_bot()
        assert elsewhere in world.channels

    async def test_a_reconnect_does_not_delete_active_channels(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        await world.force_new_gateway_session()
        await world.wait_for(lambda: bot.is_ready())
        await world.settle()
        assert channel_id in world.channels
        assert bot.sessions.by_owner(owner) is not None

    async def test_everything_still_works_after_a_reconnect(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # discord.py replaces its objects on a new session, so a session holding on to
        # old ones would quietly see out-of-date members and overwrites.
        owner, channel_id = await make_vc(world)
        await world.force_new_gateway_session()
        await world.settle()
        await world.click(owner, world.panel_message(channel_id), "Unlocked (click to lock)")
        assert int(world.overwrite_on(channel_id, EVERYONE_ROLE_ID)["deny"]) & CONNECT
        await world.join_voice(world.add_member("Bob"), channel_id)
        await world.settle()
        assert bot.sessions.by_owner(owner).timer_started

    async def test_changes_missed_during_a_disconnect_are_handled(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # Everyone leaves while the bot is disconnected, so it never sees those events.
        owner, channel_id = await make_vc(world)
        world.voice.pop(owner)
        await world.force_new_gateway_session()
        await world.settle()
        assert channel_id not in world.channels

    async def test_someone_already_waiting_in_the_hub_is_served_on_startup(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        waiting = world.add_member("Alice")
        world.voice[waiting] = HUB_ID
        await start_bot()
        await world.settle()
        assert world.voice[waiting] == int(world.only_temp_channel()["id"])

    async def test_only_the_guilds_and_voice_state_intents_are_requested(self, world: FakeDiscord, bot: TempVCBot) -> None:
        assert world.identify["intents"] == (1 << 0) | (1 << 7)

    async def test_events_from_other_servers_are_ignored(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        # The bot is set up for a different server from the one this event comes from.
        await start_bot(guild_id=999)
        assert "not in the server with GUILD_ID 999" in caplog.text
        await world.join_voice(world.add_member("Alice"), HUB_ID)
        await world.settle()
        assert world.temp_channels() == []


class TestRaces:
    async def test_new_channel_is_not_treated_as_a_leftover_while_being_set_up(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # The cleanup runs while the bot is still setting the new channel's permissions.
        manager = bot.channel_manager
        real_grant = manager._grant_access

        async def grant_then_clean_up(channel: discord.VoiceChannel, owner: discord.Member) -> None:
            await real_grant(channel, owner)
            await manager.delete_leftovers()

        manager._grant_access = grant_then_clean_up
        _, channel_id = await make_vc(world)
        assert channel_id in world.channels

    async def test_reconnect_during_setup_does_not_delete_the_empty_new_channel(self, world: FakeDiscord, bot: TempVCBot) -> None:
        # The new channel is empty only because its owner is still on their way in.
        cog = bot.get_cog("VoiceEvents")
        real_move = bot.channel_manager.move_member

        async def catch_up_then_move(member: discord.Member, session: Any) -> bool:
            await cog._catch_up_on_sessions()
            return await real_move(member, session)

        bot.channel_manager.move_member = catch_up_then_move
        owner, channel_id = await make_vc(world)
        assert channel_id in world.channels
        assert world.voice[owner] == channel_id

    async def test_owner_leaving_immediately_posts_no_confusing_notice(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        # The owner is gone by the time the bot posts the panel.
        from temp_vc_bot.views.control_panel import ControlPanelView

        real_post = ControlPanelView.post

        async def leave_first(view: ControlPanelView, channel: discord.VoiceChannel) -> None:
            await world.leave_voice(view.session.owner_id)
            await world.wait_for(lambda: view.session.closed)
            await real_post(view, channel)

        monkeypatch.setattr(ControlPanelView, "post", leave_first)
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.temp_channels() == []
        assert world.messages_in(HUB_ID) == []
        assert BOT_USER_ID not in world.voice

    async def test_someone_joining_during_setup_still_starts_the_timer(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch) -> None:
        # A friend arrives before the timer embed and panel exist, so the first update
        # has nothing to edit. Both messages must still end up showing the right state.
        from temp_vc_bot.views.control_panel import ControlPanelView

        real_post = ControlPanelView.post
        friend = world.add_member("Bob")

        async def friend_arrives_first(view: ControlPanelView, channel: discord.VoiceChannel) -> None:
            await world.join_voice(friend, channel.id)
            await world.wait_for(lambda: view.session.timer_started)
            await real_post(view, channel)

        monkeypatch.setattr(ControlPanelView, "post", friend_arrives_first)
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        channel_id = int(world.only_temp_channel()["id"])
        session = bot.sessions.by_owner(owner)
        assert session.timer_started
        assert shown_timestamp(world, channel_id) == int(session.ends_at.timestamp())
        assert dropdown(world, channel_id)["disabled"] is True


class TestDiscordSizeLimits:
    """Discord refuses oversized labels and titles, which the fake doesn't enforce by itself."""

    async def test_buttons_and_dropdown_fit(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world)
        for button in _buttons(world.panel_message(channel_id)):
            assert len(button["label"]) <= 80
        select = dropdown(world, channel_id)
        assert len(select["placeholder"]) <= 150
        assert len(select["options"]) <= 25
        for option in select["options"]:
            assert len(option["label"]) <= 100

    @pytest.mark.parametrize("button", ["Rename", "User limit"])
    async def test_forms_fit(self, world: FakeDiscord, bot: TempVCBot, button: str) -> None:
        owner, channel_id = await make_vc(world)
        click = await world.click(owner, world.panel_message(channel_id), button)
        assert len(click.modal["title"]) <= 45
        text_box = find_component(click.modal, "type", 4)
        assert len(text_box["label"]) <= 45
        assert text_box["min_length"] <= len(text_box["value"]) <= text_box["max_length"], "The pre-filled value must fit too."

    async def test_a_very_long_display_name_still_makes_a_valid_channel_name(self, world: FakeDiscord, bot: TempVCBot) -> None:
        _, channel_id = await make_vc(world, "x" * 200)
        assert len(world.channels[channel_id]["name"]) == 100


@pytest.fixture
def real_voice_client(world: FakeDiscord, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use discord.py's real VoiceClient. The fake has no voice server, so it can't finish.

    This depends on `world` so it runs after it, and undoes its FakeVoiceClient.
    """
    monkeypatch.setitem(discord.abc.Connectable.connect.__kwdefaults__, "cls", discord.VoiceClient)
    monkeypatch.setattr(channel_manager, "VOICE_CONNECT_TIMEOUT_SECONDS", 0.5)


@pytest.mark.usefixtures("real_voice_client")
class TestRealVoiceClient:
    async def test_failed_connection_is_cleaned_up_so_a_later_channel_can_try_again(self, world: FakeDiscord, bot: TempVCBot, caplog: pytest.LogCaptureFixture) -> None:
        _, channel_id = await make_vc(world, "Alice")
        await world.wait_for(lambda: "Could not join voice channel" in caplog.text)
        await world.settle()
        assert "TimeoutError" in caplog.text, "The log has to say why, for anyone troubleshooting."
        assert channel_id in world.channels, "The channel works without the bot in it."
        assert bot.channel_manager.guild.voice_client is None, "A dead connection must not block later ones."

        caplog.clear()
        caplog.set_level(logging.INFO)
        await make_vc(world, "Bob")
        assert "by permissions only" not in caplog.text, "The next channel should get its own attempt."

    async def test_channel_closing_while_the_bot_is_connecting_still_closes_cleanly(self, world: FakeDiscord, bot: TempVCBot, caplog: pytest.LogCaptureFixture) -> None:
        owner, channel_id = await make_vc(world)
        await world.leave_voice(owner)
        await world.wait_for(lambda: channel_id not in world.channels, timeout=5)
        await world.wait_for(lambda: bot.channel_manager.guild.voice_client is None, timeout=5)
        await world.settle()
        assert "Traceback" not in caplog.text
        assert "Ignoring exception" not in caplog.text


class TestErrors:
    async def test_missing_permissions_at_creation_tell_the_member_in_the_hub(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        world.bot_permissions = 0
        await start_bot()
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.temp_channels() == []
        notice = next(m for m in world.messages_in(HUB_ID))
        assert f"<@{owner}>" in notice["content"]
        assert "permission" in notice["content"]
        assert [int(user) for user in notice["allowed_mentions_sent"]["users"]] == [owner]

    async def test_failure_after_creating_removes_the_channel(self, world: FakeDiscord, start_bot: BotStarter) -> None:
        world.bot_permissions = INVITE_PERMISSIONS & ~(1 << 28)  # Everything except Manage Roles.
        await start_bot()
        owner = world.add_member("Alice")
        await world.join_voice(owner, HUB_ID)
        await world.settle()
        assert world.temp_channels() == [], "A channel the bot can't manage must not be left behind."
        notice = next(m for m in world.messages_in(HUB_ID))
        assert f"<@{owner}>" in notice["content"]
        assert "permission" in notice["content"]

    async def test_startup_logs_exactly_which_permissions_are_missing(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        world.bot_permissions = INVITE_PERMISSIONS & ~(1 << 24)  # Everything except Move Members.
        await start_bot()
        assert "Move Members" in caplog.text

    async def test_startup_logs_missing_attach_files_which_the_ring_needs(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        world.bot_permissions = INVITE_PERMISSIONS & ~ATTACH_FILES
        await start_bot()
        assert "Attach Files" in caplog.text

    async def test_wrong_hub_and_category_ids_are_logged(self, world: FakeDiscord, start_bot: BotStarter, caplog: pytest.LogCaptureFixture) -> None:
        del world.channels[CATEGORY_ID]
        await start_bot()
        assert "TEMP_VC_CATEGORY_ID" in caplog.text

    async def test_bot_failing_to_join_voice_does_not_break_the_channel(self, world: FakeDiscord, bot: TempVCBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        async def refuse(self: Any, **kwargs: Any) -> None:
            raise TimeoutError("Could not connect in time.")

        monkeypatch.setattr(discord.VoiceChannel, "connect", refuse)
        owner, channel_id = await make_vc(world)
        assert BOT_USER_ID not in world.voice
        assert channel_id in world.channels
        assert world.panel_message(channel_id)
        assert "Could not join voice channel" in caplog.text
        assert "Ignoring exception" not in caplog.text, "The failure should be handled, not left to crash the handler."

    async def test_action_on_a_channel_deleted_a_moment_ago_gets_a_friendly_reply(self, world: FakeDiscord, bot: TempVCBot) -> None:
        owner, channel_id = await make_vc(world)
        panel = world.panel_message(channel_id)
        click = await world.click(owner, panel, "Rename")
        session = bot.sessions.by_owner(owner)
        bot.channel_manager.guild._channels.pop(channel_id)  # As if the delete just happened.
        result = await world.submit_modal(owner, panel, click.modal, "New name")
        assert "no longer exists" in result.private_texts()[0]
        assert session is not None
