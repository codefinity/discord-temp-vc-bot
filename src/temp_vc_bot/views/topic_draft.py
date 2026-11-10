"""The /topic form, and the private card it shows to the person who used it."""

from __future__ import annotations

import logging

import discord

from ..constants import (
    ADVERTISE_LABEL,
    CREATE_VC_LABEL,
    EDIT_TOPIC_LABEL,
    EMPTY_TOPIC_MESSAGE,
    MAX_CHANNEL_NAME_LENGTH,
    MAX_CHANNEL_STATUS_LENGTH,
    MAX_TOPIC_LENGTH,
    MIN_CHANNEL_NAME_LENGTH,
    MIN_CHANNEL_STATUS_LENGTH,
    MODAL_TIMEOUT_SECONDS,
    STATUS_FIELD_LABEL,
    STATUS_FIELD_PLACEHOLDER,
    TOPIC_ADVERTISED_MESSAGE,
    TOPIC_CHANNEL_MISSING_MESSAGE,
    TOPIC_DRAFT_EXPIRED_MESSAGE,
    TOPIC_DRAFT_SECONDS,
    TOPIC_FORM_TITLE,
    TOPIC_NAME_LABEL,
    TOPIC_NAME_PLACEHOLDER,
    TOPIC_PREVIEW_MESSAGE,
    TOPIC_TEXT_LABEL,
    TOPIC_TEXT_PLACEHOLDER,
)
from ..errors import API_ERRORS, report_interaction_error
from ..services.topics import Topic, TopicService
from .topic_ad import TopicAdView
from .topic_card import build_topic_embed, open_vc_for_click, vc_ready_message

log = logging.getLogger(__name__)


class TopicForm(discord.ui.Modal, title=TOPIC_FORM_TITLE):
    """Asks for the VC's name and status, and the topic. All three are required.

    /topic opens it empty. The private card's Edit button opens it filled in.
    """

    channel_name = discord.ui.TextInput(
        label=TOPIC_NAME_LABEL,
        placeholder=TOPIC_NAME_PLACEHOLDER,
        min_length=MIN_CHANNEL_NAME_LENGTH,
        max_length=MAX_CHANNEL_NAME_LENGTH,
    )
    channel_status = discord.ui.TextInput(
        label=STATUS_FIELD_LABEL,
        placeholder=STATUS_FIELD_PLACEHOLDER,
        min_length=MIN_CHANNEL_STATUS_LENGTH,
        max_length=MAX_CHANNEL_STATUS_LENGTH,
    )
    topic_text = discord.ui.TextInput(
        label=TOPIC_TEXT_LABEL,
        placeholder=TOPIC_TEXT_PLACEHOLDER,
        style=discord.TextStyle.paragraph,
        max_length=MAX_TOPIC_LENGTH,
    )

    def __init__(self, topics: TopicService, draft: TopicDraftView | None = None) -> None:
        super().__init__(timeout=MODAL_TIMEOUT_SECONDS)
        self.topics = topics
        self.draft = draft
        if draft is not None:
            topic = draft.topic
            self.channel_name.default = topic.name
            self.channel_status.default = topic.status
            self.topic_text.default = topic.text

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Show the private card, or update it after an edit."""
        name, status, text = (box.value.strip() for box in (self.channel_name, self.channel_status, self.topic_text))
        # Discord's form counts spaces as characters, so an empty box can get this far.
        if not (name and status and text):
            await interaction.response.send_message(EMPTY_TOPIC_MESSAGE, ephemeral=True)
            return

        if self.draft is None:
            topic = Topic(host_id=interaction.user.id, name=name, status=status, text=text)
            draft = TopicDraftView(self.topics, topic, interaction)
            await interaction.response.send_message(
                TOPIC_PREVIEW_MESSAGE, embed=build_topic_embed(topic), view=draft, ephemeral=True
            )
            return

        if self.draft.is_finished():
            # The card expired while the form was open. Editing it would bring back dead buttons.
            await interaction.response.send_message(TOPIC_DRAFT_EXPIRED_MESSAGE, ephemeral=True)
            return
        topic = self.draft.topic
        topic.name, topic.status, topic.text = name, status, text
        self.draft.latest = interaction
        await interaction.response.edit_message(embed=build_topic_embed(topic), view=self.draft)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Log an unexpected error and show a short message."""
        await report_interaction_error(interaction, error)


class TopicDraftView(discord.ui.View):
    """The private card's Edit, Advertise and Create a VC buttons.

    Only the person who used /topic can see the card, so only they can click it.
    """

    def __init__(self, topics: TopicService, topic: Topic, origin: discord.Interaction) -> None:
        super().__init__(timeout=TOPIC_DRAFT_SECONDS)
        self.topics = topics
        self.topic = topic
        # The latest click or form that left this card as its answer. Only the
        # person who used /topic can see the card, so the bot can only change it
        # through one of those, and only for 15 minutes.
        self.latest = origin

    @discord.ui.button(label=EDIT_TOPIC_LABEL, emoji="✏️", style=discord.ButtonStyle.secondary)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the form again, filled in with what the card shows now."""
        await interaction.response.send_modal(TopicForm(self.topics, self))

    @discord.ui.button(label=ADVERTISE_LABEL, emoji="📣", style=discord.ButtonStyle.primary)
    async def advertise_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Post the card for everyone in the topic channel."""
        channel = self.topics.channels.topic_channel
        if channel is None:
            await interaction.response.send_message(TOPIC_CHANNEL_MISSING_MESSAGE, ephemeral=True)
            return
        # Acknowledge first: Discord fails any click not answered within 3 seconds.
        await interaction.response.defer()
        self.latest = interaction
        await TopicAdView(self.topics, self.topic).post(channel)
        self.stop()
        await interaction.edit_original_response(
            content=TOPIC_ADVERTISED_MESSAGE.format(channel=channel.mention), embed=None, view=None
        )

    @discord.ui.button(label=CREATE_VC_LABEL, emoji="🎙️", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the VC now, without advertising the topic first."""
        await interaction.response.defer()
        self.latest = interaction
        session = await open_vc_for_click(interaction, self.topics, self.topic)
        if session is None:
            return
        self.stop()
        await interaction.edit_original_response(
            content=vc_ready_message(session), embed=build_topic_embed(self.topic), view=None
        )

    async def on_timeout(self) -> None:
        """Replace the card's buttons with a note, so they can't be clicked once they've stopped working."""
        try:
            await self.latest.edit_original_response(content=TOPIC_DRAFT_EXPIRED_MESSAGE, embed=None, view=None)
        except API_ERRORS as error:
            # Most likely the person dismissed the card, or the last change to it was too long ago.
            log.debug("Could not mark a topic draft as expired: %s", error)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        """Log any error from a button and show a short message."""
        await report_interaction_error(interaction, error)
