"""Every duration, limit and label the bot uses, all in one place.

Change a value here and the whole bot follows, including the dropdown choices
and the wording of the messages.
"""

from datetime import timedelta

# ---------------------------------------------------------------------------
# Durations, in minutes
# ---------------------------------------------------------------------------
MIN_TIMER_MINUTES = 20  # Shortest choice in the "Set timer" dropdown.
MAX_TIMER_MINUTES = 40  # Longest choice in the "Set timer" dropdown.
TIMER_STEP_MINUTES = 5  # Gap between the dropdown choices.
DEFAULT_TIMER_MINUTES = 20  # Used when the owner does not pick a length.
EXTENSION_MINUTES = 10  # Added when the owner answers "Yes".
ANSWER_WINDOW_MINUTES = 2  # How long the owner has to answer the prompt.
# How often the countdown ring is redrawn. Every redraw is a new picture, and
# Discord shows a blurred preview of each new picture until it has loaded. Once
# a minute keeps those moments rare, and matches when the minutes in the middle
# change. Lower it for a smoother ring with more blurred moments, e.g. 0.25 for
# every 15 seconds. Discord allows about 5 edits per 5 seconds in a channel.
RING_REDRAW_MINUTES = 1

# These two are worked out from the values above, so they stay correct.
MAX_LIFETIME_MINUTES = MAX_TIMER_MINUTES + EXTENSION_MINUTES
TIMER_CHOICES = tuple(range(MIN_TIMER_MINUTES, MAX_TIMER_MINUTES + 1, TIMER_STEP_MINUTES))

# How many real seconds one "minute" above lasts. Keep this at 60 in real use.
# Lower it to test the timer quickly: at 6, everything runs ten times faster.
SECONDS_PER_MINUTE = 60


def minutes_to_delta(minutes: float) -> timedelta:
    """Turn a number of minutes from this file into a length of time."""
    return timedelta(seconds=minutes * SECONDS_PER_MINUTE)


# ---------------------------------------------------------------------------
# Limits set by Discord
# ---------------------------------------------------------------------------
MIN_CHANNEL_NAME_LENGTH = 1
MAX_CHANNEL_NAME_LENGTH = 100
NO_USER_LIMIT = 0  # Discord treats a user limit of 0 as "no limit".
MAX_USER_LIMIT = 99  # The most seats a voice channel can have.

# ---------------------------------------------------------------------------
# Connection settings, in seconds
# ---------------------------------------------------------------------------
# discord.py normally sleeps through a rate limit, which could freeze a rename
# for 10 minutes. Above this wait it raises an error instead, so the bot can tell
# the owner to try later. discord.py does not allow a value below 30.
MAX_RATE_LIMIT_WAIT_SECONDS = 30.0
VOICE_CONNECT_TIMEOUT_SECONDS = 30.0  # How long the bot tries to join voice.
HUB_NOTICE_SECONDS = 30  # How long an error notice stays in the hub's chat.
MODAL_TIMEOUT_SECONDS = 15 * 60  # Discord's interaction tokens last 15 minutes.
DELETE_ATTEMPTS = 3  # Tries at deleting a channel while being rate limited.

# ---------------------------------------------------------------------------
# Channel naming
# ---------------------------------------------------------------------------
CHANNEL_NAME_TEMPLATE = "{name}'s VC"

# ---------------------------------------------------------------------------
# Button and dropdown labels
# ---------------------------------------------------------------------------
RENAME_LABEL = "Rename"
USER_LIMIT_LABEL = "User limit"
# The lock button's label shows the current state, as the brief asks. It also
# says what a click does, so nobody mistakes "Locked" for the action.
LOCKED_LABEL = "Locked (click to unlock)"
UNLOCKED_LABEL = "Unlocked (click to lock)"
TIMER_PLACEHOLDER = "Set timer length"
YES_LABEL = "Yes"
NO_LABEL = "No"

# ---------------------------------------------------------------------------
# Messages shown to people
# ---------------------------------------------------------------------------
NOT_OWNER_MESSAGE = "Only the channel owner can do this."
WAITING_MESSAGE = "Waiting for someone to join…"
EXTEND_QUESTION = f"Time's up! Extend by {EXTENSION_MINUTES} minutes?"
TIMER_ALREADY_STARTED_MESSAGE = "The timer has already started, so its length can't be changed."
INVALID_NAME_MESSAGE = f"Please enter a name with {MIN_CHANNEL_NAME_LENGTH}–{MAX_CHANNEL_NAME_LENGTH} characters."
REJECTED_NAME_MESSAGE = "Discord rejected that name. Please try a different one."
INVALID_LIMIT_MESSAGE = (
    f"Please enter a whole number from {NO_USER_LIMIT} to {MAX_USER_LIMIT}. {NO_USER_LIMIT} means no limit."
)
RENAME_LIMIT_MESSAGE = (
    "Discord only allows about 2 renames per 10 minutes for each channel. Please try again in {wait}."
)
