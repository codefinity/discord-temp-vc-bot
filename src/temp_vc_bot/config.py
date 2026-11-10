"""Load the bot's settings from the .env file and check they make sense."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# The placeholder from .env.example. Seeing it means .env was never filled in.
TOKEN_PLACEHOLDER = "paste-your-bot-token-here"


class ConfigError(Exception):
    """One or more settings are missing or wrong. The message says how to fix them."""


@dataclass(frozen=True)
class Settings:
    """The values from .env, checked and converted to the right types."""

    # repr=False keeps the token out of logs if settings are ever printed.
    discord_token: str = field(repr=False)
    guild_id: int
    hub_channel_id: int
    temp_vc_category_id: int
    # The text channel where /topic works, such as #find-your-group.
    topic_channel_id: int


def load_settings() -> Settings:
    """Read .env from the current folder and return the settings.

    Raises ConfigError listing every problem at once, so someone setting up the
    bot can fix them all in one go.
    """
    env_file = Path.cwd() / ".env"
    load_dotenv(env_file)

    problems: list[str] = []
    if not env_file.exists():
        problems.append(f"No .env file found in {Path.cwd()}. Copy .env.example to .env and fill it in.")

    token = _read_token(problems)
    guild_id = _read_id("GUILD_ID", problems)
    hub_channel_id = _read_id("HUB_CHANNEL_ID", problems)
    temp_vc_category_id = _read_id("TEMP_VC_CATEGORY_ID", problems)
    topic_channel_id = _read_id("TOPIC_CHANNEL_ID", problems)

    if problems:
        raise ConfigError("\n".join(f"- {problem}" for problem in problems))

    return Settings(token, guild_id, hub_channel_id, temp_vc_category_id, topic_channel_id)


def _read_token(problems: list[str]) -> str:
    """Return the bot token, noting a problem if it is missing."""
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == TOKEN_PLACEHOLDER:
        problems.append("DISCORD_TOKEN is not set. Paste your bot's token into .env.")
    return token


def _read_id(name: str, problems: list[str]) -> int:
    """Return a Discord ID from the environment, noting a problem if it is invalid."""
    raw_value = os.getenv(name, "").strip()
    # Real Discord IDs are long numbers, so 0 can only be the example placeholder.
    if not raw_value.isdecimal() or int(raw_value) == 0:
        problems.append(f"{name} must be a Discord ID (a long number). Current value: {raw_value!r}.")
        return 0
    return int(raw_value)
