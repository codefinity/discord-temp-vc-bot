"""Tests for the small pieces that don't need Discord at all."""

from __future__ import annotations

from datetime import UTC, datetime

import discord
import pytest

from temp_vc_bot import constants, main
from temp_vc_bot.bot import build_intents
from temp_vc_bot.config import ConfigError, load_settings
from temp_vc_bot.errors import describe_wait, friendly_error
from temp_vc_bot.services.channel_manager import seats_needed
from temp_vc_bot.state import SessionRegistry, TempVCSession
from temp_vc_bot.views.modals import parse_user_limit

from .fake_discord import INVITE_PERMISSIONS


class TestConstants:
    def test_dropdown_offers_exactly_the_choices_in_the_brief(self) -> None:
        assert constants.TIMER_CHOICES == (20, 25, 30, 35, 40)
        assert constants.DEFAULT_TIMER_MINUTES == 20

    def test_durations_match_the_brief(self) -> None:
        assert constants.EXTENSION_MINUTES == 10
        assert constants.ANSWER_WINDOW_MINUTES == 2
        assert constants.MAX_LIFETIME_MINUTES == 50

    def test_extra_time_is_longer_than_the_answer_window(self) -> None:
        # Extra time counts from the end of the base time, so answering uses some of it.
        assert constants.EXTENSION_MINUTES > constants.ANSWER_WINDOW_MINUTES

    def test_extend_question_uses_the_exact_wording(self) -> None:
        assert constants.EXTEND_QUESTION == "Time's up! Extend by 10 minutes?"


class TestMaximumLifetime:
    def test_slowest_possible_yes_still_ends_at_50_minutes(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        session = TempVCSession(channel_id=1, owner_id=2, timer_minutes=constants.MAX_TIMER_MINUTES)
        session.start_timer(start)
        # The owner uses the whole 2-minute answer window before clicking Yes.
        session.await_answer(session.ends_at + constants.minutes_to_delta(constants.ANSWER_WINDOW_MINUTES))
        session.extend()
        assert session.ends_at - start == constants.minutes_to_delta(constants.MAX_LIFETIME_MINUTES)


class TestSeats:
    @pytest.mark.parametrize(
        ("requested", "bots", "expected"),
        [
            (0, 0, 0),  # No limit stays no limit...
            (0, 1, 0),  # ...even with a bot in the channel.
            (5, 0, 5),  # The bot isn't in the channel, so there's no extra seat.
            (5, 1, 6),  # The bot is in the channel, so it gets its own seat.
            (5, 2, 7),  # Another bot (say, a music bot) also gets its own.
            (98, 1, 99),  # This just fits.
            (99, 0, 99),
            (99, 1, 99),  # Discord's maximum wins, so 98 people fit.
        ],
    )
    def test_seats_needed(self, requested: int, bots: int, expected: int) -> None:
        assert seats_needed(requested, bots) == expected


class TestParseUserLimit:
    @pytest.mark.parametrize(("text", "expected"), [("0", 0), ("5", 5), ("99", 99), (" 7 ", 7), ("05", 5)])
    def test_valid(self, text: str, expected: int) -> None:
        assert parse_user_limit(text) == expected

    @pytest.mark.parametrize("text", ["", " ", "-1", "100", "abc", "1.5", "1_0", "+5"])
    def test_invalid(self, text: str) -> None:
        assert parse_user_limit(text) is None


class TestRegistry:
    def test_finds_by_channel_and_owner_and_forgets_on_remove(self) -> None:
        registry = SessionRegistry()
        session = TempVCSession(channel_id=1, owner_id=2)
        registry.add(session)
        assert registry.by_channel(1) is session
        assert registry.by_owner(2) is session
        assert registry.remove(session) is True
        assert registry.by_channel(1) is None
        assert registry.by_owner(2) is None

    def test_second_remove_reports_nothing_to_do(self) -> None:
        registry = SessionRegistry()
        session = TempVCSession(channel_id=1, owner_id=2)
        registry.add(session)
        registry.remove(session)
        assert registry.remove(session) is False


class TestIntentsAndInvite:
    def test_only_guilds_and_voice_states_are_requested(self) -> None:
        assert build_intents() == discord.Intents(guilds=True, voice_states=True)

    def test_no_privileged_intents(self) -> None:
        intents = build_intents()
        assert not (intents.members or intents.presences or intents.message_content)

    def test_invite_number_matches_the_documented_permission_list(self) -> None:
        documented = discord.Permissions(
            view_channel=True,
            manage_channels=True,
            manage_roles=True,
            connect=True,
            move_members=True,
            send_messages=True,
            embed_links=True,
        )
        assert documented.value == INVITE_PERMISSIONS


class TestFriendlyMessages:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(1, "about 1 minute"), (60, "about 1 minute"), (61, "about 2 minutes"), (540, "about 9 minutes")],
    )
    def test_describe_wait(self, seconds: float, expected: str) -> None:
        assert describe_wait(seconds) == expected

    def test_rate_limit_message_says_when_to_retry(self) -> None:
        assert "about 2 minutes" in friendly_error(discord.RateLimited(90))


class TestConfig:
    @staticmethod
    def _write_env(tmp_path, monkeypatch, text: str, encoding: str = "utf-8") -> None:
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            monkeypatch.delenv(name, raising=False)
        (tmp_path / ".env").write_text(text, encoding=encoding)
        monkeypatch.chdir(tmp_path)
        # load_dotenv fills os.environ. Have pytest undo that after each test.
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)

    VALID = "DISCORD_TOKEN=abc\nGUILD_ID=1\nHUB_CHANNEL_ID=2\nTEMP_VC_CATEGORY_ID=3\n"

    def test_valid_file(self, tmp_path, monkeypatch) -> None:
        self._write_env(tmp_path, monkeypatch, self.VALID)
        settings = load_settings()
        assert (settings.discord_token, settings.guild_id, settings.hub_channel_id, settings.temp_vc_category_id) == ("abc", 1, 2, 3)

    def test_token_never_appears_when_settings_are_printed(self, tmp_path, monkeypatch) -> None:
        self._write_env(tmp_path, monkeypatch, self.VALID)
        assert "abc" not in repr(load_settings())

    def test_file_saved_with_a_windows_byte_order_mark_still_loads(self, tmp_path, monkeypatch) -> None:
        self._write_env(tmp_path, monkeypatch, self.VALID, encoding="utf-8-sig")
        assert load_settings().discord_token == "abc"

    def test_every_problem_is_listed_at_once(self, tmp_path, monkeypatch) -> None:
        self._write_env(tmp_path, monkeypatch, "GUILD_ID=abc\n")
        with pytest.raises(ConfigError) as caught:
            load_settings()
        message = str(caught.value)
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            assert name in message

    def test_unedited_example_file_is_rejected(self, tmp_path, monkeypatch) -> None:
        example = (constants.__file__.rsplit("src", 1)[0] + ".env.example")
        with open(example, encoding="utf-8") as file:
            self._write_env(tmp_path, monkeypatch, file.read())
        with pytest.raises(ConfigError) as caught:
            load_settings()
        message = str(caught.value)
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            assert name in message

    def test_missing_file_says_how_to_create_it(self, tmp_path, monkeypatch) -> None:
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match=r"Copy \.env\.example"):
            load_settings()


class TestMain:
    def test_bad_settings_exit_with_the_code_systemd_wont_restart(self, tmp_path, monkeypatch) -> None:
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as caught:
            main.main()
        assert caught.value.code == main.EXIT_BAD_CONFIG

    def test_rejected_token_exits_with_the_same_code(self, tmp_path, monkeypatch) -> None:
        (tmp_path / ".env").write_text(TestConfig.VALID, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        for name in ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)

        def fail_login(*args: object, **kwargs: object) -> None:
            raise discord.LoginFailure("Improper token has been passed.")

        monkeypatch.setattr(main.TempVCBot, "run", fail_login)
        with pytest.raises(SystemExit) as caught:
            main.main()
        assert caught.value.code == main.EXIT_BAD_CONFIG
