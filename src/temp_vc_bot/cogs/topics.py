"""The /topic command, which starts a topic in the topic channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..constants import TOPIC_COMMAND_DESCRIPTION, WRONG_TOPIC_CHANNEL_MESSAGE
from ..services.topics import channel_link
from ..views.topic_draft import TopicForm

if TYPE_CHECKING:
    from ..bot import TempVCBot


class Topics(commands.Cog):
    """Holds the /topic command."""

    def __init__(self, bot: TempVCBot) -> None:
        self.settings = bot.settings
        self.topics = bot.topic_service

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Make sure the topic channel's topic explains /topic. This can run more than once."""
        await self.topics.describe_channel()

    @app_commands.command(name="topic", description=TOPIC_COMMAND_DESCRIPTION)
    @app_commands.guild_only()
    async def topic(self, interaction: discord.Interaction) -> None:
        """Open the topic form, but only in the topic channel."""
        topic_channel_id = self.settings.topic_channel_id
        if interaction.channel_id != topic_channel_id:
            message = WRONG_TOPIC_CHANNEL_MESSAGE.format(channel=channel_link(topic_channel_id))
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.send_modal(TopicForm(self.topics))


async def setup(bot: TempVCBot) -> None:
    """Called by discord.py when this file is loaded as an extension.

    The command is registered only to the one server the bot serves, so it never
    shows up anywhere else.
    """
    await bot.add_cog(Topics(bot), guild=discord.Object(bot.settings.guild_id))
