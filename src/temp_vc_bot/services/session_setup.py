"""Finish setting up a new temp VC, whether it came from the hub or from /topic."""

from __future__ import annotations

import logging

import discord

from ..errors import API_ERRORS, friendly_error
from ..state import TempVCSession
from ..views.control_panel import ControlPanelView
from .channel_manager import ChannelManager
from .naming import NamingService
from .timer import TimerService

log = logging.getLogger(__name__)


class SessionSetup:
    """Posts a new channel's messages and crowns its owner."""

    def __init__(self, channels: ChannelManager, timers: TimerService, naming: NamingService) -> None:
        self.channels = channels
        self.timers = timers
        self.naming = naming

    async def finish(self, owner: discord.Member, session: TempVCSession, *, ask_for_name: bool = True) -> None:
        """Post the controls, then ask the owner to name the channel, then post the timer.

        A channel from /topic already has the name its host chose, so it skips the
        naming prompt with `ask_for_name=False`.

        The naming prompt sits just above the timer. The timer is still the last
        message, so it doesn't have to move below the prompt. The bot never joins the
        voice channel itself. It manages the channel through its permissions, so it
        doesn't show up in the channel or take a seat.
        """
        channel = self.channels.get_channel(session)
        if channel is None or session.closed:
            return

        panel = ControlPanelView(self.channels, session)
        session.panel_view = panel
        try:
            await panel.post(channel)
            if ask_for_name:
                await self.naming.ask(session, channel)
            await self.timers.post_timer_message(session, channel)
        except API_ERRORS as error:
            if session.closed:
                return  # The owner left straight away, so there is nothing to report.
            log.error("Could not post the controls in channel %s: %s", channel.id, error)
            await self.channels.notify_in_hub(owner.id, friendly_error(error))
        await self.channels.crown_owner(session, owner)
        session.setup_finished = True

        # Someone may have joined while those messages were being sent.
        if session.timer_started:
            await self.timers.publish_state(session)
        else:
            await self.timers.maybe_start(session)
