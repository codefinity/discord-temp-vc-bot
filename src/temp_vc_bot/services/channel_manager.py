"""Create, change and delete temporary voice channels."""

from __future__ import annotations

import asyncio
import logging

import discord

from ..config import Settings
from ..constants import (
    CHANNEL_NAME_TEMPLATE,
    DELETE_ATTEMPTS,
    HUB_NOTICE_SECONDS,
    MAX_CHANNEL_NAME_LENGTH,
    MAX_NICKNAME_LENGTH,
    MAX_USER_LIMIT,
    NO_USER_LIMIT,
    OWNER_CROWN,
)
from ..errors import API_ERRORS, CategoryMissingError, ChannelGoneError
from ..state import SessionRegistry, TempVCSession

log = logging.getLogger(__name__)

# What the bot needs in the category, as {permission: name shown in Discord}.
REQUIRED_CATEGORY_PERMISSIONS = {
    "view_channel": "View Channels",
    "manage_channels": "Manage Channels",
    "manage_roles": "Manage Roles",
    "connect": "Connect",
    "move_members": "Move Members",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
}
# What the bot needs in the hub, to see people arrive and move them out.
REQUIRED_HUB_PERMISSIONS = {
    "view_channel": "View Channels",
    "move_members": "Move Members",
}
# What the bot needs in the topic channel, to post topic cards and invites, and to
# set the channel's topic to the guide to /topic.
REQUIRED_TOPIC_CHANNEL_PERMISSIONS = {
    "view_channel": "View Channels",
    "manage_channels": "Manage Channels",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
}


def human_members(channel: discord.VoiceChannel) -> list[discord.Member]:
    """Everyone in a voice channel who is not a bot."""
    return [member for member in channel.members if not member.bot]


def has_other_human(channel: discord.VoiceChannel, owner_id: int) -> bool:
    """Whether a human other than the owner is in the channel."""
    return any(member.id != owner_id for member in human_members(channel))


def seats_needed(requested_limit: int, bots_in_channel: int) -> int:
    """Work out the limit to set on a channel so bots don't use up people's seats.

    Discord counts bots towards a channel's limit, so each one gets an extra seat.
    Discord's maximum still applies, so the total can't go above it.
    """
    if requested_limit == NO_USER_LIMIT:
        return NO_USER_LIMIT
    return min(requested_limit + bots_in_channel, MAX_USER_LIMIT)


def crowned_nickname(display_name: str) -> str:
    """The owner's nickname with the crown in front, cut to fit Discord's limit.

    Discord may count an emoji such as the crown as two characters, the way
    JavaScript does, so the length is measured that way to be safe.
    """
    nickname = OWNER_CROWN + display_name
    while len(nickname.encode("utf-16-le")) // 2 > MAX_NICKNAME_LENGTH:
        nickname = nickname[:-1]
    return nickname


def _why_no_crown(member: discord.Member) -> str | None:
    """Why Discord won't let the bot change this member's nickname, or None if it will."""
    guild = member.guild
    me = guild.me
    if not me.guild_permissions.manage_nicknames:
        return "the bot is missing Manage Nicknames"
    if member.id == guild.owner_id:
        return "they own the server, and Discord never lets bots change the owner's nickname"
    if member.top_role >= me.top_role:
        return f"their highest role, {member.top_role.name}, isn't below the bot's role"
    return None


def _member_overwrite_ids(channel: discord.VoiceChannel) -> list[int]:
    """The members, other than the bot, with their own overwrite on a channel.

    On a temp VC that's the owner, who gets one so they can always get in.
    """
    me_id = channel.guild.me.id
    ids = []
    for target in channel.overwrites:
        is_role = isinstance(target, discord.Role) or (isinstance(target, discord.Object) and target.type is discord.Role)
        if not is_role and target.id != me_id:
            ids.append(target.id)
    return ids


def _bot_overwrite(me: discord.Member) -> discord.PermissionOverwrite:
    """The permissions the bot gives itself on each temp VC."""
    overwrite = discord.PermissionOverwrite(
        view_channel=True,
        # The bot joins only to play a /timer's beeps and ring. Discord also won't let
        # anyone edit a voice channel they can't connect to, so this keeps a locked
        # channel manageable.
        connect=True,
        manage_channels=True,
        move_members=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,  # For the countdown ring, which is an image.
    )
    # Discord only lets server administrators put "Manage Permissions" in a channel
    # overwrite. A bot without Administrator already has it on every channel through
    # its server-level "Manage Roles" permission. See "Conflicts" in the README.
    if me.guild_permissions.administrator:
        overwrite.manage_permissions = True
    # For the status owners can set. Discord refuses an overwrite that grants a
    # permission the bot doesn't have in the server, so a bot invited before this
    # was added would lose its whole overwrite, and with it every new channel.
    if me.guild_permissions.set_voice_channel_status:
        overwrite.set_voice_channel_status = True
    # For a /timer's beeps and ring, even in a category where members can't speak.
    # Checked for the same reason as the status.
    if me.guild_permissions.speak:
        overwrite.speak = True
    return overwrite


class ChannelManager:
    """All the calls to Discord that touch a temp VC."""

    def __init__(self, bot: discord.Client, settings: Settings, sessions: SessionRegistry) -> None:
        self.bot = bot
        self.settings = settings
        self.sessions = sessions
        # Held while checking whether someone already owns a channel and creating one
        # for them. Otherwise a double click on the hub, or the hub and /topic at the
        # same moment, could give one person two channels, because creating takes a moment.
        self.creation_lock = asyncio.Lock()

    # ----- Looking things up ------------------------------------------------

    @property
    def guild(self) -> discord.Guild | None:
        """The server this bot is set up for."""
        return self.bot.get_guild(self.settings.guild_id)

    def get_channel(self, session: TempVCSession) -> discord.VoiceChannel | None:
        """Find a session's voice channel, or None if it is gone."""
        return self.voice_channel(session.channel_id)

    def voice_channel(self, channel_id: int) -> discord.VoiceChannel | None:
        """Find a voice channel by its ID, or None if it is gone."""
        guild = self.guild
        channel = guild.get_channel(channel_id) if guild else None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @property
    def topic_channel(self) -> discord.TextChannel | None:
        """The text channel where /topic works and topic cards are posted."""
        guild = self.guild
        channel = guild.get_channel(self.settings.topic_channel_id) if guild else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def require_channel(self, session: TempVCSession) -> discord.VoiceChannel:
        """Find a session's voice channel, or raise an error if it is gone."""
        channel = self.get_channel(session)
        if channel is None:
            raise ChannelGoneError(f"Channel {session.channel_id} no longer exists.")
        return channel

    # ----- Creating ---------------------------------------------------------

    async def create_session(self, owner: discord.Member, *, name: str | None = None) -> TempVCSession:
        """Create a temp VC for `owner` and start tracking it.

        Without a `name`, the channel is named after the owner until they rename it.
        """
        category = owner.guild.get_channel(self.settings.temp_vc_category_id)
        if not isinstance(category, discord.CategoryChannel):
            raise CategoryMissingError(f"No category with ID {self.settings.temp_vc_category_id}.")

        name = (name or CHANNEL_NAME_TEMPLATE.format(name=owner.display_name))[:MAX_CHANNEL_NAME_LENGTH]
        # Passing no overwrites here makes the channel copy its category's permissions,
        # so a private category stays private. The bot's and owner's are added after.
        channel = await owner.guild.create_voice_channel(name, category=category, reason="Temp VC created")

        # Start tracking it right away, so the startup cleanup running at the same
        # moment can't mistake this new channel for a leftover.
        session = TempVCSession(channel_id=channel.id, owner_id=owner.id)
        self.sessions.add(session)
        try:
            await self._grant_access(channel, owner)
        except API_ERRORS:
            # The bot can't manage a channel without these, so don't leave it behind.
            await self.close_session(session, "its permissions could not be set")
            raise

        log.info("Created temp VC %s for member %s.", channel.id, owner.id)
        return session

    async def _grant_access(self, channel: discord.VoiceChannel, owner: discord.Member) -> None:
        """Give the bot its admin-level access, and make sure the owner can always get in."""
        me = channel.guild.me
        await channel.set_permissions(me, overwrite=_bot_overwrite(me), reason="Temp VC bot access")
        await channel.set_permissions(
            owner,
            overwrite=discord.PermissionOverwrite(view_channel=True, connect=True),
            reason="Temp VC owner access",
        )

    # ----- Moving people ----------------------------------------------------

    async def move_member(self, member: discord.Member, session: TempVCSession) -> bool:
        """Move a member into a temp VC. Returns False if that didn't work."""
        channel = self.get_channel(session)
        if channel is None:
            return False
        try:
            await member.move_to(channel, reason="Temp VC move")
        except API_ERRORS as error:
            # The usual cause: the member left voice before the move happened.
            log.warning("Could not move member %s into channel %s: %s", member.id, channel.id, error)
            return False
        return True

    async def notify_in_hub(self, member_id: int, text: str) -> None:
        """Post a short notice in the hub's text chat, for when the member has no temp VC."""
        guild = self.guild
        hub = guild.get_channel(self.settings.hub_channel_id) if guild else None
        if not isinstance(hub, discord.VoiceChannel):
            return
        try:
            await hub.send(
                f"<@{member_id}> {text}",
                delete_after=HUB_NOTICE_SECONDS,
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(member_id)]),
            )
        except API_ERRORS as error:
            log.warning("Could not post a notice in the hub: %s", error)

    # ----- Owner controls ---------------------------------------------------
    # These raise errors, so the buttons and forms can show the owner what happened.

    async def rename(self, session: TempVCSession, new_name: str) -> None:
        """Rename a temp VC. Discord allows only about 2 renames per 10 minutes."""
        channel = self.require_channel(session)
        if channel.name != new_name:  # Don't spend a rename on no change.
            await channel.edit(name=new_name, reason="Temp VC renamed by owner")
        # Any name the owner chooses counts, even the one it already had.
        session.named.set()

    async def set_status(self, session: TempVCSession, status: str) -> None:
        """Set the line Discord shows under the channel's name.

        This isn't a rename, so it doesn't use up one of the renames. The bot can
        set it without joining the channel because it also has Manage Channels there.
        """
        channel = self.require_channel(session)
        await channel.edit(status=status, reason="Temp VC status set by owner")
        session.status = status

    async def set_user_limit(self, session: TempVCSession, requested_limit: int) -> int:
        """Set how many people may join. Returns how many actually fit."""
        channel = self.require_channel(session)
        people_who_fit = await self._write_limit(channel, requested_limit)
        session.requested_limit = requested_limit
        return people_who_fit

    async def set_locked(self, session: TempVCSession, locked: bool) -> None:
        """Lock or unlock a temp VC.

        People inside stay, because Discord only checks Connect when someone joins.
        The owner and the bot have their own overwrites, so they can still connect.
        """
        channel = self.require_channel(session)
        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        # On unlock, None means "follow the category". Setting True instead would let
        # everyone in, even if the category is private.
        overwrite.connect = False if locked else None
        await channel.set_permissions(
            everyone,
            overwrite=None if overwrite.is_empty() else overwrite,
            reason="Temp VC locked" if locked else "Temp VC unlocked",
        )
        session.locked = locked

    # ----- Seats for bots ---------------------------------------------------

    async def refresh_user_limit(self, session: TempVCSession) -> None:
        """Re-apply a channel's limit after a bot, such as a music bot, joins or leaves it."""
        channel = self.get_channel(session)
        if channel is None:
            return
        try:
            await self._write_limit(channel, session.requested_limit)
        except API_ERRORS as error:
            log.warning("Could not update the user limit of channel %s: %s", channel.id, error)

    async def _write_limit(self, channel: discord.VoiceChannel, requested_limit: int) -> int:
        """Set a channel's limit, adding seats for bots. Returns how many people fit.

        This bot gets no seat. It only drops in for a few seconds to play a /timer's
        sounds, and Move Members lets it into a full channel anyway.
        """
        me = channel.guild.me
        bots_in_channel = sum(member.bot and member.id != me.id for member in channel.members)
        seats = seats_needed(requested_limit, bots_in_channel)
        if channel.user_limit != seats:
            await channel.edit(user_limit=seats, reason="Temp VC user limit")
        return seats - bots_in_channel

    # ----- Closing ----------------------------------------------------------

    async def close_session(self, session: TempVCSession, reason: str) -> None:
        """Cancel a session's timer and delete the channel.

        It is safe to call this twice. Whichever call comes second does nothing.
        """
        if not self.sessions.remove(session):
            return
        session.closed = True
        log.info("Closing temp VC %s: %s.", session.channel_id, reason)
        session.stop_background_work()
        # Before the delete: if the bot stopped in between, the startup cleanup finds
        # the owner through the leftover channel and takes the crown off then.
        await self.remove_crown(session)
        channel = self.get_channel(session)
        if channel is not None:
            await self._delete_channel(channel)

    async def _delete_channel(self, channel: discord.VoiceChannel) -> None:
        """Delete a channel, waiting and retrying if Discord is rate limiting us."""
        for _ in range(DELETE_ATTEMPTS):
            try:
                await channel.delete(reason="Temp VC closed")
                return
            except discord.NotFound:
                return  # Already gone, which is what we wanted.
            except discord.RateLimited as error:
                log.warning("Rate limited deleting channel %s. Retrying in %.0fs.", channel.id, error.retry_after)
                await asyncio.sleep(error.retry_after)
            except API_ERRORS as error:
                log.error("Could not delete channel %s: %s", channel.id, error)
                return
        log.error("Gave up deleting channel %s after %d attempts.", channel.id, DELETE_ATTEMPTS)

    # ----- The owner's crown ------------------------------------------------

    async def crown_owner(self, session: TempVCSession, owner: discord.Member) -> None:
        """Put a crown in front of the owner's nickname while they own the channel.

        Owners Discord won't let the bot rename, such as the server owner, just
        don't get one. The channel works the same either way.
        """
        if session.closed:
            return
        reason = _why_no_crown(owner)
        if reason is not None:
            log.info("No crown for member %s: %s.", owner.id, reason)
            return
        try:
            # Fetched fresh: discord.py keeps its copy of a member while they're in
            # voice, and without the Members intent the bot hears nothing when their
            # nickname changes. Crowning an out-of-date nickname would undo their
            # change when the channel closes and that one is given back.
            member = await owner.guild.fetch_member(owner.id)
            if member.display_name.startswith(OWNER_CROWN):
                return
            original, crowned = member.nick, crowned_nickname(member.display_name)
            await member.edit(nick=crowned, reason="Temp VC owner")
        except API_ERRORS as error:
            log.warning("Could not give member %s the owner's crown: %s", owner.id, error)
            return
        session.original_nick, session.crowned_nick = original, crowned
        if session.closed:
            # The channel closed during the edit, when there was no crown yet to remove.
            await self.remove_crown(session)

    async def remove_crown(self, session: TempVCSession) -> None:
        """Give the owner back their old nickname, unless they've changed it since."""
        crowned, session.crowned_nick = session.crowned_nick, None
        guild = self.guild
        if crowned is None or guild is None:
            return
        try:
            # Fetched fresh: without the Members intent, the bot hears nothing when a
            # nickname changes, so the copy it keeps can be out of date.
            member = await guild.fetch_member(session.owner_id)
            if member.nick == crowned:
                await member.edit(nick=session.original_nick, reason="Temp VC closed")
        except API_ERRORS as error:
            log.warning("Could not take the crown off member %s: %s", session.owner_id, error)

    async def _remove_leftover_crown(self, guild: discord.Guild, member_id: int) -> None:
        """Take the crown off an owner from before a restart.

        Their old nickname was lost in the restart. If what's left after the crown
        is just their account's name, they most likely had no nickname, so it's cleared.
        """
        try:
            member = await guild.fetch_member(member_id)
            if member.nick is None or not member.nick.startswith(OWNER_CROWN):
                return
            rest = member.nick.removeprefix(OWNER_CROWN)
            nick = None if rest == (member.global_name or member.name) else rest
            await member.edit(nick=nick, reason="Temp VC closed before a restart")
        except API_ERRORS as error:
            log.warning("Could not take the crown off member %s after a restart: %s", member_id, error)

    # ----- Startup ----------------------------------------------------------

    async def delete_leftovers(self) -> None:
        """Delete temp VCs left over from before a restart."""
        guild = self.guild
        category = guild.get_channel(self.settings.temp_vc_category_id) if guild else None
        if not isinstance(category, discord.CategoryChannel):
            return
        for channel in category.voice_channels:
            if self._is_leftover(channel):
                log.info("Deleting leftover temp VC %s from before a restart.", channel.id)
                for member_id in _member_overwrite_ids(channel):
                    await self._remove_leftover_crown(channel.guild, member_id)
                await self._delete_channel(channel)

    def _is_leftover(self, channel: discord.VoiceChannel) -> bool:
        """Recognise a temp VC by the permission the bot gave itself when creating it.

        Only channels carrying that mark are touched, so the hub and any permanent
        channels someone keeps in the same category are safe.
        """
        if channel.id == self.settings.hub_channel_id or self.sessions.by_channel(channel.id):
            return False
        return channel.overwrites_for(channel.guild.me).manage_channels is True

    def report_setup_problems(self) -> None:
        """Log a clear message about anything that would stop the bot from working."""
        guild = self.guild
        if guild is None:
            guild_id = self.settings.guild_id
            log.error("The bot is not in the server with GUILD_ID %s. Check .env and the invite.", guild_id)
            return

        if not guild.me.guild_permissions.manage_nicknames:
            log.warning(
                "The bot is missing Manage Nicknames, so channel owners won't get a crown. "
                "Open the invite link again to add it."
            )
        if not guild.me.guild_permissions.set_voice_channel_status:
            log.warning(
                "The bot is missing Set Voice Channel Status, so owners can't set a channel status. "
                "Open the invite link again to add it."
            )
        if not guild.me.guild_permissions.speak:
            log.warning(
                "The bot is missing Speak, so /timer can't beep or ring. Open the invite link again to add it."
            )

        hub = guild.get_channel(self.settings.hub_channel_id)
        if isinstance(hub, discord.VoiceChannel):
            _log_missing_permissions("the hub", hub.permissions_for(guild.me), REQUIRED_HUB_PERMISSIONS)
        else:
            log.error("HUB_CHANNEL_ID %s is not a voice channel in this server.", self.settings.hub_channel_id)

        category = guild.get_channel(self.settings.temp_vc_category_id)
        if isinstance(category, discord.CategoryChannel):
            _log_missing_permissions("the category", category.permissions_for(guild.me), REQUIRED_CATEGORY_PERMISSIONS)
        else:
            log.error("TEMP_VC_CATEGORY_ID %s is not a category in this server.", self.settings.temp_vc_category_id)

        topic_channel = self.topic_channel
        if topic_channel is not None:
            permissions = topic_channel.permissions_for(guild.me)
            _log_missing_permissions("the topic channel", permissions, REQUIRED_TOPIC_CHANNEL_PERMISSIONS)
        else:
            log.error("TOPIC_CHANNEL_ID %s is not a text channel in this server.", self.settings.topic_channel_id)


def _log_missing_permissions(place: str, permissions: discord.Permissions, required: dict[str, str]) -> None:
    """Log the name of any permission the bot lacks in `place`."""
    missing = [label for name, label in required.items() if not getattr(permissions, name)]
    if missing:
        log.error("The bot is missing these permissions in %s: %s.", place, ", ".join(missing))
