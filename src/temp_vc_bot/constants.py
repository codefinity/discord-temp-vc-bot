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
# How long the owner has to name a new channel. If they don't, it's deleted.
NAME_DEADLINE_MINUTES = 2
# How often the countdown ring is redrawn. Every redraw is a new picture, and
# Discord shows a blurred preview of each new picture until it has loaded. Once
# a minute keeps those moments rare, and matches when the minutes in the middle
# change. Lower it for a smoother ring with more blurred moments, e.g. 0.25 for
# every 15 seconds. Discord allows about 5 edits per 5 seconds in a channel.
RING_REDRAW_MINUTES = 1
# When people chat below the timer, the bot waits this many seconds, then posts
# the timer again at the bottom of the chat and deletes the old one, so it stays
# in view. Anything posted during the wait is covered by the same repost, so a busy
# chat costs one repost per wait, not one per message. Each repost is a new picture,
# so a shorter wait also means more blurred previews.
TIMER_REPOST_DELAY_SECONDS = 5

# These two are worked out from the values above, so they stay correct.
MAX_LIFETIME_MINUTES = MAX_TIMER_MINUTES + EXTENSION_MINUTES
TIMER_CHOICES = tuple(range(MIN_TIMER_MINUTES, MAX_TIMER_MINUTES + 1, TIMER_STEP_MINUTES))

# How many real seconds one "minute" above lasts. Keep this at 60 in real use.
# Lower it to test the timer quickly: at 6, everything runs ten times faster.
SECONDS_PER_MINUTE = 60
#SECONDS_PER_MINUTE = 4


def minutes_to_delta(minutes: float) -> timedelta:
    """Turn a number of minutes from this file into a length of time."""
    return timedelta(seconds=minutes * SECONDS_PER_MINUTE)


# ---------------------------------------------------------------------------
# Limits set by Discord
# ---------------------------------------------------------------------------
MIN_CHANNEL_NAME_LENGTH = 1
MAX_CHANNEL_NAME_LENGTH = 100
# A voice channel's status is the line Discord shows under its name in the channel list.
MAX_CHANNEL_STATUS_LENGTH = 500
NO_USER_LIMIT = 0  # Discord treats a user limit of 0 as "no limit".
MAX_USER_LIMIT = 99  # The most seats a voice channel can have.
MAX_NICKNAME_LENGTH = 32

# ---------------------------------------------------------------------------
# Connection settings, in seconds
# ---------------------------------------------------------------------------
# discord.py normally sleeps through a rate limit, which could freeze a rename
# for 10 minutes. Above this wait it raises an error instead, so the bot can tell
# the owner to try later. discord.py does not allow a value below 30.
MAX_RATE_LIMIT_WAIT_SECONDS = 90.0
HUB_NOTICE_SECONDS = 30  # How long an error notice stays in the hub's chat.
END_CONFIRM_SECONDS = 60  # How long the owner has to confirm ending the channel early.
MODAL_TIMEOUT_SECONDS = 15 * 60  # Discord's interaction tokens last 15 minutes.
DELETE_ATTEMPTS = 3  # Tries at deleting a channel while being rate limited.

# ---------------------------------------------------------------------------
# Channel naming
# ---------------------------------------------------------------------------
# The name a channel has until its owner names it.
CHANNEL_NAME_TEMPLATE = "{name}'s VC"
# Owners must give a status along with the name. Discord itself allows none.
MIN_CHANNEL_STATUS_LENGTH = 1
# Put in front of the owner's nickname while they own a channel, so people can
# see who it is. Changing this also changes what the startup cleanup looks for.
OWNER_CROWN = "👑 "

# ---------------------------------------------------------------------------
# Button and dropdown labels
# ---------------------------------------------------------------------------
RENAME_LABEL = "Rename"
NAME_BUTTON_LABEL = "Name your channel"
NAME_FORM_TITLE = "Name your channel"
# The second box in the naming and rename forms. The owner has to fill it in.
STATUS_FIELD_LABEL = "Channel status"
STATUS_FIELD_PLACEHOLDER = "Shown under the channel's name, e.g. Revising for exams"
USER_LIMIT_LABEL = "User limit"
# The lock button's label shows the current state, as the brief asks. It also
# says what a click does, so nobody mistakes "Locked" for the action.
LOCKED_LABEL = "Locked (click to unlock)"
UNLOCKED_LABEL = "Unlocked (click to lock)"
TIMER_PLACEHOLDER = "Set timer length"
YES_LABEL = "Yes"
NO_LABEL = "No"
END_LABEL = "End discussion"
END_CONFIRM_LABEL = "End now"
END_CANCEL_LABEL = "Keep it open"

# ---------------------------------------------------------------------------
# Messages shown to people
# ---------------------------------------------------------------------------
NOT_OWNER_MESSAGE = "Only the channel owner can do this."
NAME_PROMPT_MESSAGE = (
    "{mention} please name your channel. If it isn't named within {minutes} minutes ({deadline}), it will be deleted."
)
# Posted in the hub, after the owner's tag.
UNNAMED_CLOSED_MESSAGE = (
    "your channel was deleted because it wasn't named in time. Join the hub to get a new one, then name it."
)
WAITING_MESSAGE = "Waiting for someone to join…"
EXTEND_QUESTION = f"Time's up! Extend by {EXTENSION_MINUTES} minutes?"
END_QUESTION = "End the discussion now? This channel will be deleted and everyone in it disconnected."
ENDING_MESSAGE = "👋 Ending the discussion. This channel is being deleted."
END_CANCELLED_MESSAGE = "👍 The channel stays open."
END_EXPIRED_MESSAGE = "No answer, so the channel stays open. Click End discussion again if you still want to end it."
TIMER_ALREADY_STARTED_MESSAGE = "The timer has already started, so its length can't be changed."
INVALID_NAME_MESSAGE = f"Please enter a name with {MIN_CHANNEL_NAME_LENGTH}–{MAX_CHANNEL_NAME_LENGTH} characters."
REJECTED_NAME_MESSAGE = "Discord rejected that name. Please try a different one."
INVALID_STATUS_MESSAGE = (
    f"Please enter a status with {MIN_CHANNEL_STATUS_LENGTH}–{MAX_CHANNEL_STATUS_LENGTH} characters."
)
REJECTED_STATUS_MESSAGE = "Discord rejected that status. Please try a different one."
STATUS_PERMISSION_MESSAGE = (
    "I couldn't set the status because I'm missing the Set Voice Channel Status permission. "
    "Please ask a server admin to open the bot's invite link again."
)
INVALID_LIMIT_MESSAGE = (
    f"Please enter a whole number from {NO_USER_LIMIT} to {MAX_USER_LIMIT}. {NO_USER_LIMIT} means no limit."
)
RENAME_LIMIT_MESSAGE = (
    "Discord only allows about 2 renames per 10 minutes for each channel. Please try again in {wait}."
)
