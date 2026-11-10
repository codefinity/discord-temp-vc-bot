"""The public topic card that Advertise posts in the topic channel."""

from __future__ import annotations

import logging

import discord

from ..constants import (
    CREATE_VC_LABEL,
    HOST_INTERESTED_MESSAGE,
    INTERESTED_LABEL,
    NOT_HOST_MESSAGE,
    TOPIC_HAS_VC_MESSAGE,
    TOPIC_IDLE_MINUTES,
    minutes_to_delta,
)
from ..errors import API_ERRORS, report_interaction_error
from ..services.topics import Topic, TopicService, channel_link
from .topic_card import build_topic_embed, open_vc_for_click, vc_ready_message

log = logging.getLogger(__name__)


class TopicAdView(discord.ui.View):
    """I'm interested, for anyone, and Create a VC, for the topic's host only.

    discord.py restarts the timeout on every click, so the card closes once
    nobody has clicked it for TOPIC_IDLE_MINUTES.
    """

    def __init__(self, topics: TopicService, topic: Topic) -> None:
        super().__init__(timeout=minutes_to_delta(TOPIC_IDLE_MINUTES).total_seconds())
        self.topics = topics
        self.topic = topic
        self.message: discord.Message | None = None

    async def post(self, channel: discord.TextChannel) -> None:
        """Post the card for everyone in the channel."""
        self.message = await channel.send(embed=build_topic_embed(self.topic), view=self)
        log.info("Member %s advertised a topic in channel %s.", self.topic.host_id, channel.id)

    @discord.ui.button(label=INTERESTED_LABEL, emoji="🙋", style=discord.ButtonStyle.primary)
    async def interested_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Add the clicker to the interested list, or take them off it if they're already on."""
        topic = self.topic
        if topic.vc_channel_id is not None:
            # Someone whose screen hasn't caught up yet. Point them to the VC instead.
            message = TOPIC_HAS_VC_MESSAGE.format(channel=channel_link(topic.vc_channel_id))
            await interaction.response.send_message(message, ephemeral=True)
            return
        if interaction.user.id == topic.host_id:
            await interaction.response.send_message(HOST_INTERESTED_MESSAGE, ephemeral=True)
            return
        topic.toggle_interest(interaction.user.id)
        await interaction.response.edit_message(embed=build_topic_embed(topic), view=self)

    @discord.ui.button(label=CREATE_VC_LABEL, emoji="🎙️", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the VC and invite everyone interested. Only the host can do this."""
        if interaction.user.id != self.topic.host_id:
            await interaction.response.send_message(NOT_HOST_MESSAGE, ephemeral=True)
            return
        # Acknowledge first: Discord fails any click not answered within 3 seconds.
        await interaction.response.defer(thinking=True, ephemeral=True)
        session = await open_vc_for_click(interaction, self.topics, self.topic)
        if session is None:
            return
        self.stop()
        await self._show_final_card()
        await interaction.followup.send(vc_ready_message(session), ephemeral=True)

    async def on_timeout(self) -> None:
        """Close a card nobody has clicked for a while, so its buttons don't linger.

        The VC may already exist, opened from the private card while this one was
        posted. Then the card shows the VC instead of closing.
        """
        if self.topic.creating_vc:
            return  # Create a VC updates the card itself when it's done.
        if self.topic.vc_channel_id is None:
            self.topic.closed = True
        await self._show_final_card()

    async def _show_final_card(self) -> None:
        """Show the card's final state, with the VC or a closing note, and no buttons."""
        if self.message is None:
            return
        try:
            await self.message.edit(embed=build_topic_embed(self.topic), view=None)
        except API_ERRORS as error:
            log.warning("Could not update the topic card %s: %s", self.message.id, error)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """Log any error from a button and show the person who clicked a short message."""
        await report_interaction_error(interaction, error)
