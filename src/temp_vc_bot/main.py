"""The entry point: `uv run temp-vc-bot` starts here."""

import logging
import sys

import discord

from .bot import TempVCBot
from .config import ConfigError, load_settings

log = logging.getLogger(__name__)

# The standard exit code for "bad configuration". The systemd service in HOSTING.md
# doesn't restart on this code, because restarting can't fix a wrong token.
EXIT_BAD_CONFIG = 78


def _setup_logging() -> None:
    """Send log messages to the terminal, or to the system journal on a server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main() -> None:
    """Load the settings and run the bot until it is stopped."""
    _setup_logging()

    try:
        settings = load_settings()
    except ConfigError as error:
        log.critical("The bot can't start. Please fix these settings in .env:\n%s", error)
        sys.exit(EXIT_BAD_CONFIG)

    try:
        # log_handler=None tells discord.py to use the logging set up above.
        TempVCBot(settings).run(settings.discord_token, log_handler=None)
    except discord.LoginFailure:
        log.critical("Discord rejected DISCORD_TOKEN. Reset the token in the Developer Portal and update .env.")
        sys.exit(EXIT_BAD_CONFIG)


if __name__ == "__main__":
    main()
