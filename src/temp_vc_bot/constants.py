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

# ---------------------------------------------------------------------------
# /topic, in the topic channel (TOPIC_CHANNEL_ID in .env)
# ---------------------------------------------------------------------------
TOPIC_COMMAND_DESCRIPTION = "Suggest something to talk about, see who's interested, then open a VC for it."
# An advertised topic closes after this many minutes with no clicks on its card.
TOPIC_IDLE_MINUTES = 60
# A topic's VC is deleted if nobody joins it within this many minutes. Its host
# may not be in voice when they create it, so nobody can be moved in.
TOPIC_JOIN_DEADLINE_MINUTES = 5
# How long the private card's buttons keep working after the last click, in seconds.
# The bot edits that card through the last click, and Discord only allows that for
# 15 minutes, so this stays a minute below MODAL_TIMEOUT_SECONDS.
TOPIC_DRAFT_SECONDS = 14 * 60
MAX_TOPIC_LENGTH = 1000
# The card names this many interested people, then says how many more there are.
MAX_INTERESTED_SHOWN = 30
# People pinged per invite message. Discord allows 100 per message, and this many
# mentions stay well inside its 2,000-character limit.
INVITES_PER_MESSAGE = 50

TOPIC_FORM_TITLE = "Start a topic"
TOPIC_NAME_LABEL = "Channel name"
TOPIC_NAME_PLACEHOLDER = "e.g. Exam revision"
TOPIC_TEXT_LABEL = "What do you want to discuss?"
TOPIC_TEXT_PLACEHOLDER = "A sentence or two, so people know what they're joining"
EDIT_TOPIC_LABEL = "Edit"
ADVERTISE_LABEL = "Advertise"
CREATE_VC_LABEL = "Create a VC"
INTERESTED_LABEL = "I'm interested"

WRONG_TOPIC_CHANNEL_MESSAGE = "/topic only works in {channel}."
EMPTY_TOPIC_MESSAGE = "Please fill in all three boxes. Spaces on their own don't count."
TOPIC_PREVIEW_MESSAGE = (
    "Only you can see this card. **Advertise** posts it in the chat, so people can say they're interested. "
    "**Create a VC** opens a voice channel for it now."
)
TOPIC_ADVERTISED_MESSAGE = (
    "📣 Your topic is posted in {channel}. When you're ready, click **Create a VC** on it, "
    "and everyone who's interested will be invited."
)
TOPIC_FOOTER_OPEN = "Click I'm interested to be invited when the VC opens. Click it again to take it back."
TOPIC_FOOTER_VC_OPEN = "The VC is open. Click the channel above to join."
TOPIC_FOOTER_CLOSED = "This topic has closed. Use /topic to start a new one."
NOT_HOST_MESSAGE = "Only the person who started this topic can do this."
HOST_INTERESTED_MESSAGE = "You started this topic, so you're already in."
TOPIC_HAS_VC_MESSAGE = "This topic already has a VC: {channel}"
TOPIC_VC_BEING_CREATED_MESSAGE = "The VC for this topic is already being created."
ALREADY_OWN_VC_MESSAGE = "You already have a temporary VC: {channel}. End it before creating another one."
TOPIC_VC_READY_MESSAGE = "✅ Your VC is ready: {channel}"
TOPIC_VC_JOIN_SOON_MESSAGE = " If nobody joins within {minutes} minutes, it will be deleted."
TOPIC_INVITE_MESSAGE = "{mentions} **{name}** is open, and you said you're interested. Join here: {channel}"
TOPIC_DRAFT_EXPIRED_MESSAGE = "This draft has expired. Use /topic to start again."
TOPIC_CHANNEL_MISSING_MESSAGE = "I can't find the channel for topics. Please tell a server admin."
# The topic channel's own topic: the line Discord shows at the top of the channel,
# next to its name. A bot can't show a message to one person when they open a
# channel, so this is where the guide to /topic goes. The bot sets it on every start
# if it's different. Discord allows up to 1,024 characters.
TOPIC_CHANNEL_DESCRIPTION = (
    "👋 Looking for people to talk to? Type **/topic** to start a topic: you'll be asked for a channel name, "
    "a status and what you'd like to talk about. **Advertise** it here, and everyone who clicks "
    "**I'm interested** is pinged with a link when you click **Create a VC**. "
    "⚠️ Please don't write anything else in this channel."
)
