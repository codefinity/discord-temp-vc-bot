"""Tests for the small pieces that don't need Discord at all."""

from __future__ import annotations

import io
import math
from datetime import UTC, datetime, timedelta

import discord
import pytest
from PIL import Image

from temp_vc_bot import constants, countdown_ring, main, sounds
from temp_vc_bot.bot import build_intents
from temp_vc_bot.config import ConfigError, load_settings
from temp_vc_bot.countdown_ring import render_ring
from temp_vc_bot.errors import describe_wait, friendly_error
from temp_vc_bot.services.channel_manager import seats_needed
from temp_vc_bot.services.timer import redraw_times, ring_face
from temp_vc_bot.services.user_timer import clock, user_timer_face
from temp_vc_bot.state import SessionRegistry, TempVCSession, TimerPhase, UserTimer, UserTimerPhase
from temp_vc_bot.views.modals import parse_user_limit
from temp_vc_bot.views.user_timer import UserTimerView

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


START = datetime(2026, 1, 1, tzinfo=UTC)


def minutes(amount: float) -> timedelta:
    return constants.minutes_to_delta(amount)


class TestRingFace:
    """What the ring shows: (fraction left, big text, small text)."""

    def test_waiting(self) -> None:
        session = TempVCSession(channel_id=1, owner_id=2)
        assert ring_face(session, START) == (1.0, "Waiting", "for someone to join")

    def test_running_rounds_the_minutes_up(self) -> None:
        session = TempVCSession(channel_id=1, owner_id=2, timer_minutes=20)
        session.start_timer(START)
        assert ring_face(session, START) == (1.0, "20", "min left")
        assert ring_face(session, START + minutes(5.5)) == (14.5 / 20, "15", "min left")
        assert ring_face(session, START + minutes(6)) == (14 / 20, "14", "min left")
        assert ring_face(session, START + minutes(19.9))[1] == "1", "It shows 1 until the very end, not 0."

    def test_answer_window_counts_down_the_two_minutes(self) -> None:
        session = TempVCSession(channel_id=1, owner_id=2)
        session.start_timer(START)
        time_up = session.ends_at
        session.await_answer(time_up + minutes(constants.ANSWER_WINDOW_MINUTES))
        assert ring_face(session, time_up) == (1.0, "2", "min to answer")
        assert ring_face(session, time_up + minutes(1.5)) == (0.25, "1", "min to answer")

    def test_extra_time_is_measured_against_the_10_extra_minutes(self) -> None:
        session = TempVCSession(channel_id=1, owner_id=2)
        session.start_timer(START)
        time_up = session.ends_at
        session.await_answer(time_up + minutes(2))
        session.extend()
        # The owner answered after 1 minute, so 9 of the 10 extra minutes are left.
        assert ring_face(session, time_up + minutes(1)) == (0.9, "9", "min left")

    def test_ending_and_overdue_both_show_an_empty_ring(self) -> None:
        session = TempVCSession(channel_id=1, owner_id=2)
        session.start_timer(START)
        assert ring_face(session, session.ends_at + minutes(1)) == (0.0, "0", "min left")
        session.phase = TimerPhase.ENDING
        assert ring_face(session, START) == (0.0, "0", "closing")


class TestRedrawTimes:
    def test_counted_back_from_the_deadline_in_whole_steps(self) -> None:
        step = minutes(constants.RING_REDRAW_MINUTES)
        deadline = START + minutes(1)
        # Starting a little off-step, the first redraw comes early to get back in line.
        now = deadline - 4 * step + timedelta(seconds=1)
        assert redraw_times(deadline, now) == [deadline - 3 * step, deadline - 2 * step, deadline - step]

    def test_now_and_the_deadline_itself_are_left_out(self) -> None:
        step = minutes(constants.RING_REDRAW_MINUTES)
        deadline = START + minutes(1)
        # The caller has just drawn "now", and the phase change at the deadline draws too.
        assert redraw_times(deadline, deadline - 2 * step) == [deadline - step]

    @pytest.mark.parametrize("seconds_left", [0, -5, 1])
    def test_nothing_to_redraw_when_less_than_a_step_is_left(self, seconds_left: float) -> None:
        deadline = START + minutes(1)
        assert redraw_times(deadline, deadline - timedelta(seconds=seconds_left)) == []

    def test_a_20_minute_timer_is_redrawn_once_a_minute_as_the_number_changes(self) -> None:
        # Each redraw shows Discord's blurred loading preview for a moment, so there
        # should be no redraws that don't change the minutes shown.
        session = TempVCSession(channel_id=1, owner_id=2, timer_minutes=20)
        session.start_timer(START)
        times = redraw_times(session.ends_at, START)
        assert len(times) == 19
        assert {later - earlier for earlier, later in zip(times, times[1:])} == {timedelta(minutes=1)}
        shown = [ring_face(session, moment)[1] for moment in times]
        assert shown == [str(n) for n in range(19, 0, -1)]


class TestRenderRing:
    GREEN = (46, 204, 113)

    def draw(self, fraction_left: float) -> Image.Image:
        return Image.open(io.BytesIO(render_ring(fraction_left, self.GREEN, "15", "min left"))).convert("RGBA")

    def ring_pixel(self, image: Image.Image, clock_hour: float) -> tuple[int, ...]:
        """The pixel in the middle of the ring's thickness, at a position on a clock face."""
        angle = math.radians(clock_hour * 30 - 90)
        radius = image.width * (0.5 - countdown_ring._MARGIN - countdown_ring._RING_WIDTH / 2)
        centre = image.width / 2
        return image.getpixel((round(centre + radius * math.cos(angle)), round(centre + radius * math.sin(angle))))

    def test_is_a_square_png_with_see_through_corners(self) -> None:
        image = self.draw(0.5)
        assert image.size == (countdown_ring.SIZE, countdown_ring.SIZE)
        assert image.getpixel((0, 0))[3] == 0, "Only the round dial is drawn, so it suits any theme."

    def test_coloured_part_shrinks_clockwise_from_12(self) -> None:
        # A quarter left: coloured from 9 o'clock to 12, and the rest is bare track.
        image = self.draw(0.25)
        assert self.ring_pixel(image, 10.5)[:3] == self.GREEN
        assert self.ring_pixel(image, 3)[:3] == countdown_ring._TRACK
        assert self.ring_pixel(image, 7)[:3] == countdown_ring._TRACK

    @pytest.mark.parametrize(("fraction_left", "coloured"), [(1.0, True), (1.5, True), (0.0, False), (-1.0, False)])
    def test_fraction_is_kept_between_empty_and_full(self, fraction_left: float, coloured: bool) -> None:
        pixel = self.ring_pixel(self.draw(fraction_left), 6)[:3]
        assert pixel == (self.GREEN if coloured else countdown_ring._TRACK)

    def test_long_text_is_shrunk_to_fit_inside_the_ring(self) -> None:
        png = render_ring(1.0, self.GREEN, "Waiting for a very long time", "and a long caption to match it")
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        # The ring at 3 and 9 o'clock is still pure ring colour, so no text spilled onto it.
        assert self.ring_pixel(image, 3)[:3] == self.GREEN
        assert self.ring_pixel(image, 9)[:3] == self.GREEN


class TestSeats:
    @pytest.mark.parametrize(
        ("requested", "bots", "expected"),
        [
            (0, 0, 0),  # No limit stays no limit...
            (0, 1, 0),  # ...even with a bot in the channel.
            (5, 0, 5),  # No bot in the channel, so there's no extra seat.
            (5, 1, 6),  # A bot (say, a music bot) gets its own seat.
            (5, 2, 7),  # So does each further bot.
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
    def test_only_guilds_voice_states_and_guild_messages_are_requested(self) -> None:
        assert build_intents() == discord.Intents(guilds=True, voice_states=True, guild_messages=True)

    def test_no_privileged_intents(self) -> None:
        intents = build_intents()
        assert not (intents.members or intents.presences or intents.message_content)

    def test_invite_number_matches_the_documented_permission_list(self) -> None:
        documented = discord.Permissions(
            view_channel=True,
            manage_channels=True,
            manage_roles=True,
            connect=True,
            speak=True,
            move_members=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            manage_nicknames=True,
            set_voice_channel_status=True,
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


SETTING_NAMES = ("DISCORD_TOKEN", "GUILD_ID", "HUB_CHANNEL_ID", "TEMP_VC_CATEGORY_ID", "TOPIC_CHANNEL_ID")


class TestConfig:
    @staticmethod
    def _write_env(tmp_path, monkeypatch, text: str, encoding: str = "utf-8") -> None:
        for name in SETTING_NAMES:
            monkeypatch.delenv(name, raising=False)
        (tmp_path / ".env").write_text(text, encoding=encoding)
        monkeypatch.chdir(tmp_path)
        # load_dotenv fills os.environ. Have pytest undo that after each test.
        for name in SETTING_NAMES:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)

    VALID = "DISCORD_TOKEN=abc\nGUILD_ID=1\nHUB_CHANNEL_ID=2\nTEMP_VC_CATEGORY_ID=3\nTOPIC_CHANNEL_ID=4\n"

    def test_valid_file(self, tmp_path, monkeypatch) -> None:
        self._write_env(tmp_path, monkeypatch, self.VALID)
        settings = load_settings()
        values = (settings.discord_token, settings.guild_id, settings.hub_channel_id, settings.temp_vc_category_id)
        assert values == ("abc", 1, 2, 3)
        assert settings.topic_channel_id == 4

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
        for name in SETTING_NAMES:
            assert name in message

    def test_unedited_example_file_is_rejected(self, tmp_path, monkeypatch) -> None:
        example = (constants.__file__.rsplit("src", 1)[0] + ".env.example")
        with open(example, encoding="utf-8") as file:
            self._write_env(tmp_path, monkeypatch, file.read())
        with pytest.raises(ConfigError) as caught:
            load_settings()
        message = str(caught.value)
        for name in SETTING_NAMES:
            assert name in message

    def test_missing_file_says_how_to_create_it(self, tmp_path, monkeypatch) -> None:
        for name in SETTING_NAMES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError, match=r"Copy \.env\.example"):
            load_settings()


class TestMain:
    def test_bad_settings_exit_with_the_code_systemd_wont_restart(self, tmp_path, monkeypatch) -> None:
        for name in SETTING_NAMES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as caught:
            main.main()
        assert caught.value.code == main.EXIT_BAD_CONFIG

    def test_rejected_token_exits_with_the_same_code(self, tmp_path, monkeypatch) -> None:
        (tmp_path / ".env").write_text(TestConfig.VALID, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        for name in SETTING_NAMES:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)

        def fail_login(*args: object, **kwargs: object) -> None:
            raise discord.LoginFailure("Improper token has been passed.")

        monkeypatch.setattr(main.TempVCBot, "run", fail_login)
        with pytest.raises(SystemExit) as caught:
            main.main()
        assert caught.value.code == main.EXIT_BAD_CONFIG


class TestSounds:
    """The beeps and the ring /timer plays. Discord voice takes 16-bit, 48 kHz stereo."""

    def test_sounds_are_whole_20_millisecond_pieces(self) -> None:
        # discord.py stops at a piece that's cut short, which would lose the end of a sound.
        for sound in (sounds.count_in(3, 1.0), sounds.ring(3)):
            assert len(sound) % sounds.FRAME_BYTES == 0

    def test_count_in_beeps_on_each_number_then_once_more_to_start(self) -> None:
        sound = sounds.count_in(3, 1.0)
        # Three beeps a second apart, then the start beep, after the lead-in.
        lead_in = sounds.LEAD_IN_SECONDS
        assert sounds.duration(sound) == pytest.approx(lead_in + 3 + 0.4, abs=0.02)
        for beep_at in (lead_in, lead_in + 1, lead_in + 2, lead_in + 3):
            assert _loudness(sound, beep_at + 0.05) > 1000, f"Expected a beep at {beep_at}s."
        for quiet_at in (lead_in + 0.5, lead_in + 1.5, lead_in + 2.5):
            assert _loudness(sound, quiet_at) == 0, f"Expected silence at {quiet_at}s."

    def test_ring_lasts_as_long_as_asked(self) -> None:
        assert sounds.duration(sounds.ring(3)) == pytest.approx(3, abs=0.02)
        assert _loudness(sounds.ring(3), 1.5) > 1000

    def test_sounds_are_not_too_loud(self) -> None:
        loudest = max(abs(sample) for sample in _samples(sounds.ring(3)))
        assert loudest < 0.5 * 32767


def _samples(pcm: bytes) -> memoryview:
    return memoryview(pcm).cast("h")


def _loudness(pcm: bytes, at_seconds: float) -> int:
    """The loudest sample in the 10 ms from `at_seconds`."""
    start = round(at_seconds * sounds.SAMPLE_RATE) * 2  # Two samples per moment, for stereo.
    return max(abs(sample) for sample in _samples(pcm)[start : start + 960])


class TestUserTimerClock:
    def test_minutes_and_seconds_rounded_up(self) -> None:
        assert clock(minutes(5)) == "5:00"
        assert clock(minutes(4.5)) == "4:30"
        assert clock(timedelta(seconds=59.2)) == "1:00", "It shows 0:00 only when time is up."
        assert clock(timedelta(0)) == "0:00"

    def test_a_hair_over_a_whole_second_isnt_rounded_up(self) -> None:
        assert clock(timedelta(seconds=50.0000001)) == "0:50"


def user_timer(minutes_long: int = 5) -> UserTimer:
    return UserTimer(channel_id=1, set_by=2, minutes=minutes_long)


class TestUserTimerFace:
    def test_ready_shows_the_full_time(self) -> None:
        assert user_timer_face(user_timer(), START) == (1.0, "5:00", "ready")

    def test_count_in_shows_the_time_then_the_numbers(self) -> None:
        timer = user_timer()
        timer.count_in()
        assert user_timer_face(timer, START) == (1.0, "5:00", "get ready")
        timer.count = 3
        assert user_timer_face(timer, START) == (1.0, "3", "get ready")

    def test_running_empties_the_ring(self) -> None:
        timer = user_timer()
        timer.run(START)
        assert user_timer_face(timer, START + minutes(1)) == (0.8, "4:00", "left")

    def test_paused_and_finished(self) -> None:
        timer = user_timer()
        timer.run(START)
        timer.pause(START + minutes(2.5))
        assert user_timer_face(timer, START + minutes(10)) == (0.5, "2:30", "paused")
        timer.finish()
        assert user_timer_face(timer, START) == (0.0, "0:00", "time's up")


class TestUserTimerState:
    def test_pause_holds_the_time_left_and_carrying_on_counts_it_down(self) -> None:
        timer = user_timer()
        timer.run(START)
        timer.pause(START + minutes(2))
        assert (timer.phase, timer.remaining) == (UserTimerPhase.PAUSED, minutes(3))
        later = START + minutes(60)
        timer.run(later)
        assert timer.ends_at == later + minutes(3)

    def test_pausing_during_the_first_count_in_goes_back_to_ready(self) -> None:
        timer = user_timer()
        timer.count_in()
        timer.pause(START)
        assert (timer.phase, timer.remaining) == (UserTimerPhase.READY, timer.length)

    def test_pausing_during_a_later_count_in_stays_paused(self) -> None:
        timer = user_timer()
        timer.run(START)
        timer.pause(START + minutes(1))
        timer.count_in()
        timer.pause(START + minutes(30))  # The count-in doesn't use up any time.
        assert (timer.phase, timer.remaining) == (UserTimerPhase.PAUSED, minutes(4))

    def test_time_left_never_goes_above_what_was_left_when_it_started(self) -> None:
        # asyncio can wake a few milliseconds before the moment the countdown began.
        timer = user_timer()
        timer.run(START)
        assert timer.time_left(START - timedelta(milliseconds=8)) == timer.length

    def test_stop_and_finish_are_over(self) -> None:
        stopped, finished = user_timer(), user_timer()
        stopped.run(START)
        stopped.stop(START + minutes(1))
        finished.finish()
        assert stopped.over and finished.over
        assert stopped.remaining == minutes(4)


class TestUserTimerButtons:
    @pytest.mark.parametrize(
        ("phase", "start", "pause"),
        [
            (UserTimerPhase.READY, True, False),
            (UserTimerPhase.COUNTING_IN, False, True),
            (UserTimerPhase.RUNNING, False, True),
            (UserTimerPhase.PAUSED, True, False),
        ],
    )
    async def test_only_buttons_that_do_something_can_be_clicked(self, phase: UserTimerPhase, start: bool, pause: bool) -> None:
        timer = user_timer()
        timer.phase = phase
        view = UserTimerView(None, timer)  # type: ignore[arg-type]
        assert (not view.start_button.disabled, not view.pause_button.disabled, not view.stop_button.disabled) == (start, pause, True)


class TestUserTimerRedrawTimes:
    def test_ten_second_steps_counted_back_from_the_end(self) -> None:
        step = timedelta(seconds=10)
        deadline = START + timedelta(minutes=1)
        assert redraw_times(deadline, START, step) == [deadline - n * step for n in range(5, 0, -1)]
