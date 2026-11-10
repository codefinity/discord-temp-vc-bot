"""Stop discord.py from refusing requests that Discord would accept.

The bot sets `max_ratelimit_timeout` (see constants.py), so that a long rate limit
raises an error at once instead of freezing a rename for 10 minutes. But once a
reply from Discord says an endpoint's rate-limit window resets further away than
that, discord.py 2.7.1 refuses every request to that endpoint until the window
ends. It never checks whether the window still has requests left.

Discord counts the new channels in a server over 24 hours, and its reply to each new
channel says so. So after the first temp VC, discord.py refused every other one for
the rest of the day ("Too many requests. Retry in 86251.43 seconds.") without asking
Discord. A restart forgot the window, which let exactly one more channel through.

This fix skips that early refusal while the window has requests left. When they run
out, nothing changes: Discord answers 429 and the bot gets the RateLimited error at
once.
"""

from __future__ import annotations

import logging

from discord import http

log = logging.getLogger(__name__)

_library_acquire = http.Ratelimit.acquire
# The parts of discord.py's rate-limit bucket that the fix reads or changes.
_ATTRIBUTES_USED = ("remaining", "expires", "_max_ratelimit_timeout")


async def _acquire(self: http.Ratelimit) -> None:
    """Take a request from a rate-limit window, refusing early only if none are left."""
    if self.remaining <= 0 or self.is_expired() or self._max_ratelimit_timeout is None:
        await _library_acquire(self)
        return
    # With requests left, the library takes one without awaiting anything, so no
    # other task can run while the timeout is switched off.
    timeout, self._max_ratelimit_timeout = self._max_ratelimit_timeout, None
    try:
        await _library_acquire(self)
    finally:
        self._max_ratelimit_timeout = timeout


def apply() -> None:
    """Install the fix. Calling it again does nothing."""
    if http.Ratelimit.acquire is _acquire:
        return
    if not all(name in http.Ratelimit.__slots__ for name in _ATTRIBUTES_USED):
        log.warning(
            "This version of discord.py is not the one the rate-limit fix was written for, so "
            "the fix is off. Only one temp VC per day may work. Run the tests to check."
        )
        return
    http.Ratelimit.acquire = _acquire  # type: ignore[method-assign]
