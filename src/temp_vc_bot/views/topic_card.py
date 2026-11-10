"""What a topic card shows, and the work behind its Create a VC button.

The private card from /topic and the public one from Advertise look the same,
so people see exactly what they're about to post.
"""

from __future__ import annotations

import discord

from ..constants import (
    MAX_INTERESTED_SHOWN,
    TOPIC_FOOTER_CLOSED,
    TOPIC_FOOTER_OPEN,
    TOPIC_FOOTER_VC_OPEN,
    TOPIC_JOIN_DEADLINE_MINUTES,
    TOPIC_VC_JOIN_SOON_MESSAGE,
    TOPIC_VC_READY_MESSAGE,
)
from ..services.topics import CannotOpenVC, Topic, TopicService, channel_link
from ..state import TempVCSession


def interested_list(topic: Topic) -> str:
    """Name the interested people, up to a limit, so the card stays within Discord's field size."""
    member_ids = list(topic.interested_ids)
    if not member_ids:
        return "Nobody yet"
    shown = ", ".join(f"<@{member_id}>" for member_id in member_ids[:MAX_INTERESTED_SHOWN])
    hidden = len(member_ids) - MAX_INTERESTED_SHOWN
    return f"{shown} and {hidden} more" if hidden > 0 else shown


def build_topic_embed(topic: Topic) -> discord.Embed:
    """Build the card from the topic's current state.

    Mentions in an embed never ping anyone, so listing people here is safe.
    """
    embed = discord.Embed(title=f"💬 {topic.name}", description=topic.text, colour=discord.Colour.red())
    embed.add_field(name="Status", value=topic.status, inline=False)
    embed.add_field(name="Started by", value=f"<@{topic.host_id}>")
    embed.add_field(name=f"Interested ({len(topic.interested_ids)})", value=interested_list(topic))
    if topic.vc_channel_id is not None:
        embed.add_field(name="Voice channel", value=channel_link(topic.vc_channel_id), inline=False)
        embed.set_footer(text=TOPIC_FOOTER_VC_OPEN)
    elif topic.closed:
        embed.set_footer(text=TOPIC_FOOTER_CLOSED)
    else:
        embed.set_footer(text=TOPIC_FOOTER_OPEN)
    return embed


async def open_vc_for_click(
    interaction: discord.Interaction, topics: TopicService, topic: Topic
) -> TempVCSession | None:
    """Do the Create a VC button's work. The click must already be acknowledged.

    Returns None, after telling the host why, if no VC was made. Discord's own
    errors are raised, for the view's on_error to report.
    """
    host = interaction.user
    assert isinstance(host, discord.Member), "/topic only works in a server."
    try:
        return await topics.open_vc(topic, host)
    except CannotOpenVC as reason:
        await interaction.followup.send(str(reason), ephemeral=True)
        return None


def vc_ready_message(session: TempVCSession) -> str:
    """Tell the host where their VC is, and warn them if it's waiting for them to join."""
    message = TOPIC_VC_READY_MESSAGE.format(channel=channel_link(session.channel_id))
    if session.waiting_for_first_join:
        message += TOPIC_VC_JOIN_SOON_MESSAGE.format(minutes=f"{TOPIC_JOIN_DEADLINE_MINUTES:g}")
    return message
