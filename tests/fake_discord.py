"""A small stand-in for Discord's servers, so tests can run the real bot with no token.

The real discord.py library talks to this over real HTTP and a real websocket, so
the tests also cover the library's own behaviour: rate limits, caching and button
routing. The fake enforces the Discord rules this bot depends on, as written in
Discord's documentation:

- Only permissions the bot holds can be granted in an overwrite, and only server
  administrators can grant MANAGE_ROLES.
- Losing Connect on a voice channel also takes away Manage Channels and so on.
- A locked or full channel refuses people, unless they have Move Members.
- Moving someone who isn't in voice fails.
- A channel can be renamed about twice per 10 minutes, then Discord answers 429.
- Uploading a file needs Attach Files. An edit's attachment list replaces the
  message's attachments: old ones not listed are removed.
- Every new message is reported over the gateway, the bot's own included. The
  order of that report and the reply to the post isn't guaranteed, so the fake
  sends the report first, which is the harder case for the bot.

It does not handle voice audio. FakeVoiceClient stands in for that part, which is
why a real voice connection can only be checked on a real server.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import discord
from aiohttp import web

ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
CONNECT = 1 << 20
SPEAK = 1 << 21
MOVE_MEMBERS = 1 << 24
MANAGE_ROLES = 1 << 28

# The exact number in the invite link in DEPLOYMENT.md.
INVITE_PERMISSIONS = 286313488
EVERYONE_PERMISSIONS = VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | READ_MESSAGE_HISTORY | CONNECT | SPEAK

GUILD_ID = 1000
EVERYONE_ROLE_ID = GUILD_ID  # Discord gives @everyone the same ID as the server.
BOT_ROLE_ID = 1001
BOT_USER_ID = 2000
HUB_ID = 3000
CATEGORY_ID = 3001

TEXT_INPUT = 4
TOKEN = "fake-token"
RENAMES_ALLOWED = 2
RENAME_RETRY_AFTER_SECONDS = 540.0

EPHEMERAL_FLAG = 64

# Files uploaded with a request, as {index: (filename, bytes)}.
UPLOADS = web.RequestKey("uploads", dict)


class FakeVoiceClient(discord.VoiceProtocol):
    """Joins and leaves voice over the gateway, but skips the audio connection."""

    async def on_voice_state_update(self, data: dict[str, Any]) -> None:
        if data["channel_id"] is None:
            self.cleanup()

    async def on_voice_server_update(self, data: dict[str, Any]) -> None:
        pass

    async def connect(self, *, timeout: float, reconnect: bool, self_deaf: bool = False, self_mute: bool = False) -> None:
        await self.channel.guild.change_voice_state(channel=self.channel, self_deaf=self_deaf, self_mute=self_mute)

    async def disconnect(self, *, force: bool) -> None:
        await self.channel.guild.change_voice_state(channel=None)
        self.cleanup()


@dataclass
class InteractionRecord:
    """Everything the bot replied to one click or form submission."""

    id: int
    token: str
    message_id: int | None
    channel_id: int
    responses: list[dict[str, Any]] = field(default_factory=list)
    followups: list[dict[str, Any]] = field(default_factory=list)
    # The new message the bot posted as its reply, if it replied with one rather
    # than by editing the clicked message. Its buttons can be clicked in turn.
    reply: dict[str, Any] | None = None

    @property
    def acknowledged(self) -> bool:
        return bool(self.responses)

    @property
    def modal(self) -> dict[str, Any]:
        """The form the bot opened in response to this click."""
        return next(r["data"] for r in self.responses if r["type"] == 9)

    def private_texts(self) -> list[str]:
        """The private (ephemeral) messages only the clicking person could see."""
        replies = [r.get("data", {}) for r in self.responses if r["type"] == 4] + self.followups
        return [reply.get("content", "") for reply in replies if reply.get("flags", 0) & EPHEMERAL_FLAG]

    def public_texts(self) -> list[str]:
        """Any reply everyone in the channel could see."""
        replies = [r.get("data", {}) for r in self.responses if r["type"] == 4] + self.followups
        return [reply.get("content", "") for reply in replies if not reply.get("flags", 0) & EPHEMERAL_FLAG]


class FakeDiscord:
    """Discord's REST API and gateway, held in memory."""

    def __init__(self, *, bot_permissions: int = INVITE_PERMISSIONS) -> None:
        self.bot_permissions = bot_permissions
        self.channels: dict[int, dict[str, Any]] = {}
        self.members: dict[int, dict[str, Any]] = {}
        self.voice: dict[int, int] = {}  # Member ID -> voice channel ID.
        self.bot_voice_flags: dict[str, bool] = {}
        self.bot_left_voice_itself = False  # True if it left, rather than being disconnected.
        self.messages: dict[int, dict[str, Any]] = {}
        self.uploaded_files: dict[str, bytes] = {}  # Attachment ID -> the file's bytes.
        self.interactions: dict[str, InteractionRecord] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.identify: dict[str, Any] | None = None
        self.identify_count = 0
        self.renames: defaultdict[int, list[float]] = defaultdict(list)
        # Tests can use this to make the second rename report an exhausted bucket,
        # as Discord's headers can. discord.py used to sleep for 10 minutes on that.
        self.report_exhausted_rename_bucket = False
        self.refuse_to_move: set[int] = set()  # Member IDs whose moves fail.
        self.channel_create_delay = 0.0  # Seconds, for testing what happens during creation.
        self._ids = itertools.count(5000)
        self._sequence = itertools.count(1)
        self._ws: web.WebSocketResponse | None = None
        self._runner: web.AppRunner | None = None
        self.port = 0

        self.members[BOT_USER_ID] = self._member_payload(BOT_USER_ID, "TempVC", bot=True, roles=[str(BOT_ROLE_ID)])
        self.add_channel("Create Your Own VC", channel_id=HUB_ID, channel_type=2)
        self.add_channel("Temp VCs", channel_id=CATEGORY_ID, channel_type=4)

    # ----- Building the world -----------------------------------------------

    def next_id(self) -> int:
        return next(self._ids)

    def add_member(self, name: str, *, bot: bool = False) -> int:
        member_id = self.next_id()
        self.members[member_id] = self._member_payload(member_id, name, bot=bot, roles=[])
        return member_id

    def add_channel(
        self,
        name: str,
        *,
        channel_id: int | None = None,
        channel_type: int = 2,
        parent_id: int | None = None,
        overwrites: list[dict[str, Any]] | None = None,
    ) -> int:
        channel_id = channel_id if channel_id is not None else self.next_id()
        self.channels[channel_id] = self._channel_payload(channel_id, name, channel_type, parent_id, overwrites or [])
        return channel_id

    def _member_payload(self, member_id: int, name: str, *, bot: bool, roles: list[str]) -> dict[str, Any]:
        return {
            "user": self._user_payload(member_id, name, bot=bot),
            "roles": roles,
            "joined_at": "2026-01-01T00:00:00+00:00",
            "deaf": False,
            "mute": False,
            "flags": 0,
            "pending": False,
            "permissions": "0",
        }

    @staticmethod
    def _user_payload(user_id: int, name: str, *, bot: bool) -> dict[str, Any]:
        return {
            "id": str(user_id),
            "username": name,
            "global_name": name,
            "discriminator": "0",
            "avatar": None,
            "bot": bot,
            "flags": 0,
            "public_flags": 0,
            "verified": True,
            "mfa_enabled": False,
        }

    @staticmethod
    def _channel_payload(
        channel_id: int, name: str, channel_type: int, parent_id: int | None, overwrites: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(channel_id),
            "guild_id": str(GUILD_ID),
            "type": channel_type,
            "name": name,
            "position": 0,
            "parent_id": str(parent_id) if parent_id else None,
            "permission_overwrites": overwrites,
            "nsfw": False,
            "flags": 0,
        }
        if channel_type == 2:
            payload |= {
                "bitrate": 64000,
                "user_limit": 0,
                "rtc_region": None,
                "video_quality_mode": 1,
                "last_message_id": None,
                "rate_limit_per_user": 0,
            }
        return payload

    def _guild_payload(self) -> dict[str, Any]:
        roles = [
            {"id": str(EVERYONE_ROLE_ID), "name": "@everyone", "permissions": str(EVERYONE_PERMISSIONS), "position": 0},
            {"id": str(BOT_ROLE_ID), "name": "TempVC", "permissions": str(self.bot_permissions), "position": 1},
        ]
        return {
            "id": str(GUILD_ID),
            "name": "Test server",
            "owner_id": "1",
            "unavailable": False,
            "member_count": len(self.members),
            "large": False,
            "roles": roles,
            "emojis": [],
            "stickers": [],
            "channels": list(self.channels.values()),
            "threads": [],
            "stage_instances": [],
            "guild_scheduled_events": [],
            "soundboard_sounds": [],
            "members": list(self.members.values()),
            "voice_states": [self._voice_state(uid, cid) for uid, cid in self.voice.items()],
            "presences": [],
            "features": [],
            "joined_at": "2026-01-01T00:00:00+00:00",
        }

    def _voice_state(self, member_id: int, channel_id: int | None) -> dict[str, Any]:
        is_bot = member_id == BOT_USER_ID
        return {
            "guild_id": str(GUILD_ID),
            "channel_id": str(channel_id) if channel_id else None,
            "user_id": str(member_id),
            "member": self.members[member_id],
            "session_id": "fake-voice-session",
            "deaf": False,
            "mute": False,
            "self_deaf": self.bot_voice_flags.get("self_deaf", False) if is_bot else False,
            "self_mute": self.bot_voice_flags.get("self_mute", False) if is_bot else False,
            "self_video": False,
            "suppress": False,
            "request_to_speak_timestamp": None,
        }

    # ----- Permissions, by Discord's documented rules ------------------------

    def _base_permissions(self, member_id: int) -> int:
        permissions = EVERYONE_PERMISSIONS
        if member_id == BOT_USER_ID:
            permissions |= self.bot_permissions
        return permissions

    def _is_admin(self, member_id: int) -> bool:
        return bool(self._base_permissions(member_id) & ADMINISTRATOR)

    def effective_permissions(self, channel_id: int, member_id: int) -> int:
        """Work out a member's permissions in a channel, the way Discord does."""
        if self._is_admin(member_id):
            return (1 << 50) - 1
        permissions = self._base_permissions(member_id)
        channel = self.channels[channel_id]
        overwrites = {int(o["id"]): o for o in channel["permission_overwrites"]}
        role_ids = [EVERYONE_ROLE_ID]
        if member_id == BOT_USER_ID:
            role_ids.append(BOT_ROLE_ID)
        for target_id in (*role_ids, member_id):
            overwrite = overwrites.get(target_id)
            if overwrite:
                permissions = (permissions & ~int(overwrite.get("deny", 0))) | int(overwrite.get("allow", 0))
        # "Denying the CONNECT permission also implicitly denies other permissions."
        if channel["type"] == 2 and not permissions & CONNECT:
            permissions &= ~(MANAGE_CHANNELS | MANAGE_ROLES | MOVE_MEMBERS)
        return permissions

    def can_join(self, member_id: int, channel_id: int) -> bool:
        """Whether Discord would let this person click into a voice channel."""
        permissions = self.effective_permissions(channel_id, member_id)
        if not permissions & CONNECT:
            return False
        limit = self.channels[channel_id]["user_limit"]
        full = limit > 0 and len(self.people_in(channel_id)) >= limit
        return not full or bool(permissions & MOVE_MEMBERS)

    def _forbidden_overwrite(self, allow: int, deny: int) -> web.Response | None:
        """Refuse an overwrite that Discord's rules wouldn't let the bot set."""
        # "only permissions your bot has in the guild can be allowed/denied"
        if (allow | deny) & ~self._base_permissions(BOT_USER_ID) and not self._is_admin(BOT_USER_ID):
            return _error(403, 50013, "Missing Permissions")
        # "Setting MANAGE_ROLES permission in channels is only possible for guild administrators."
        if (allow | deny) & MANAGE_ROLES and not self._is_admin(BOT_USER_ID):
            return _error(403, 50013, "Missing Permissions")
        return None

    # ----- Asking about the world, for test assertions -----------------------

    def people_in(self, channel_id: int) -> list[int]:
        return [member_id for member_id, voice_channel in self.voice.items() if voice_channel == channel_id]

    def temp_channels(self) -> list[dict[str, Any]]:
        """All voice channels in the category except the hub and any named Permanent."""
        return [
            c
            for c in self.channels.values()
            if c["type"] == 2 and c["parent_id"] == str(CATEGORY_ID) and int(c["id"]) != HUB_ID and "Permanent" not in c["name"]
        ]

    def only_temp_channel(self) -> dict[str, Any]:
        channels = self.temp_channels()
        assert len(channels) == 1, f"Expected exactly one temp channel, found {[c['name'] for c in channels]}"
        return channels[0]

    def overwrite_on(self, channel_id: int, target_id: int) -> dict[str, Any] | None:
        for overwrite in self.channels[channel_id]["permission_overwrites"]:
            if int(overwrite["id"]) == target_id:
                return overwrite
        return None

    def messages_in(self, channel_id: int) -> list[dict[str, Any]]:
        return [m for m in self.messages.values() if int(m["channel_id"]) == channel_id]

    def panel_message(self, channel_id: int) -> dict[str, Any]:
        return next(m for m in self.messages_in(channel_id) if m["components"] and _find_all(m, "label", "Rename"))

    def timer_message(self, channel_id: int) -> dict[str, Any]:
        return next(m for m in self.messages_in(channel_id) if m["embeds"] and "Timer" in m["embeds"][0].get("title", ""))

    def timer_ring(self, channel_id: int) -> bytes:
        """The image currently attached to the timer message."""
        attachments = self.timer_message(channel_id)["attachments"]
        assert len(attachments) == 1, f"Expected one image on the timer, found {len(attachments)}."
        return self.uploaded_files[attachments[0]["id"]]

    def prompt_message(self, channel_id: int) -> dict[str, Any] | None:
        for message in self.messages_in(channel_id):
            if "Time's up! Extend" in message["content"]:
                return message
        return None

    def edits_of(self, message_id: int) -> int:
        """How many times the bot edited a message through the regular API."""
        return sum(1 for method, path, _ in self.requests if method == "PATCH" and path.endswith(f"/messages/{message_id}"))

    def request_count(self, method: str, path_part: str) -> int:
        return sum(1 for m, path, _ in self.requests if m == method and path_part in path)

    async def settle(self) -> None:
        """Wait until the bot has been quiet for a moment."""
        quiet_rounds = 0
        last_count = -1
        for _ in range(500):
            await asyncio.sleep(0.01)
            quiet_rounds = quiet_rounds + 1 if len(self.requests) == last_count else 0
            last_count = len(self.requests)
            if quiet_rounds >= 6:
                return

    async def wait_for(self, condition: Any, *, timeout: float = 15.0) -> None:
        """Wait until `condition()` is true, or fail the test."""
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("Timed out waiting for a condition.")
            await asyncio.sleep(0.005)

    # ----- Things people do --------------------------------------------------

    async def join_voice(self, member_id: int, channel_id: int) -> None:
        """A person enters a voice channel, moving out of any channel they're in."""
        self.voice[member_id] = channel_id
        await self._dispatch("VOICE_STATE_UPDATE", self._voice_state(member_id, channel_id))

    async def leave_voice(self, member_id: int) -> None:
        self.voice.pop(member_id, None)
        await self._dispatch("VOICE_STATE_UPDATE", self._voice_state(member_id, None))

    async def say(self, member_id: int, channel_id: int, text: str = "hello") -> dict[str, Any]:
        """A person posts a message in a channel's text chat."""
        message = self._message_payload(channel_id, {"content": text}, author_id=member_id)
        await self._add_message(message)
        return message

    async def click(self, member_id: int, message: dict[str, Any], label: str) -> InteractionRecord:
        """A person clicks the button with this label."""
        component = _find_all(message, "label", label)[0]
        data = {"custom_id": component["custom_id"], "component_type": 2}
        return await self._interact(3, member_id, message, data)

    async def choose(self, member_id: int, message: dict[str, Any], value: str) -> InteractionRecord:
        """A person picks an option in the message's dropdown."""
        select = _find_all(message, "type", 3)[0]
        data = {"custom_id": select["custom_id"], "component_type": 3, "values": [value]}
        return await self._interact(3, member_id, message, data)

    async def submit_modal(
        self, member_id: int, message: dict[str, Any], modal: dict[str, Any], value: str
    ) -> InteractionRecord:
        """A person types `value` into a form's text box and submits it."""
        text_box = _find_all(modal, "type", TEXT_INPUT)[0]
        rows = [{"type": 1, "components": [{"type": TEXT_INPUT, "custom_id": text_box["custom_id"], "value": value}]}]
        data = {"custom_id": modal["custom_id"], "components": rows}
        return await self._interact(5, member_id, message, data)

    async def delete_channel_by_hand(self, channel_id: int, *, delete_event_first: bool) -> None:
        """A server admin deletes a channel in Discord's own interface.

        The events about the channel and the people in it could arrive in either order.
        """
        if not delete_event_first:
            await self._remove_channel(channel_id)
            return
        people = self.people_in(channel_id)
        await self._dispatch("CHANNEL_DELETE", self.channels.pop(channel_id))
        for member_id in people:
            await self.leave_voice(member_id)

    async def force_new_gateway_session(self) -> None:
        """Make the bot log in again, which fires on_ready a second time."""
        assert self._ws is not None
        logins_before = self.identify_count
        await self._ws.send_json({"op": 9, "d": False})
        await self.wait_for(lambda: self.identify_count > logins_before)
        await self.settle()

    async def _interact(
        self, interaction_type: int, member_id: int, message: dict[str, Any], data: dict[str, Any]
    ) -> InteractionRecord:
        interaction_id = self.next_id()
        token = f"interaction-token-{interaction_id}"
        record = InteractionRecord(interaction_id, token, int(message["id"]), int(message["channel_id"]))
        self.interactions[token] = record
        channel = self.channels[int(message["channel_id"])]
        await self._dispatch(
            "INTERACTION_CREATE",
            {
                "id": str(interaction_id),
                "application_id": str(BOT_USER_ID),
                "type": interaction_type,
                "token": token,
                "version": 1,
                "guild_id": str(GUILD_ID),
                "channel_id": channel["id"],
                "channel": channel,
                "member": self.members[member_id],
                "app_permissions": "0",
                "locale": "en-US",
                "attachment_size_limit": 26214400,
                "entitlements": [],
                "authorizing_integration_owners": {"0": str(GUILD_ID)},
                "context": 0,
                "data": data,
                "message": message,
            },
        )
        await self.settle()
        return record

    # ----- Server plumbing ---------------------------------------------------

    async def start(self) -> None:
        app = web.Application()
        routes = [
            ("GET", "/users/@me", self._get_me),
            ("GET", "/oauth2/applications/@me", self._get_application),
            ("POST", "/guilds/{guild_id}/channels", self._create_channel),
            ("PATCH", "/guilds/{guild_id}/members/{member_id}", self._edit_member),
            ("PUT", "/channels/{channel_id}/permissions/{target_id}", self._set_overwrite),
            ("DELETE", "/channels/{channel_id}/permissions/{target_id}", self._delete_overwrite),
            ("PATCH", "/channels/{channel_id}", self._edit_channel),
            ("DELETE", "/channels/{channel_id}", self._delete_channel),
            ("POST", "/channels/{channel_id}/messages", self._create_message),
            ("PATCH", "/channels/{channel_id}/messages/{message_id}", self._edit_message),
            ("DELETE", "/channels/{channel_id}/messages/{message_id}", self._delete_message),
            ("POST", "/interactions/{interaction_id}/{token}/callback", self._interaction_callback),
            ("POST", "/webhooks/{application_id}/{token}", self._interaction_followup),
            ("PATCH", "/webhooks/{application_id}/{token}/messages/@original", self._interaction_edit_original),
        ]
        for method, path, handler in routes:
            app.router.add_route(method, f"/api/v10{path}", self._logged(handler))
        app.router.add_get("/gateway", self._gateway)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    @property
    def rest_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v10"

    @property
    def gateway_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/gateway"

    def _logged(self, handler: Any) -> Any:
        async def wrapper(request: web.Request) -> web.StreamResponse:
            body = await self._read_body(request)
            self.requests.append((request.method, request.path, body))
            if not request.path.startswith(("/api/v10/interactions", "/api/v10/webhooks")):
                if request.headers.get("Authorization") != f"Bot {TOKEN}":
                    return _error(401, 0, "401: Unauthorized")
            return await handler(request, body)

        return wrapper

    @staticmethod
    async def _read_body(request: web.Request) -> dict[str, Any]:
        """Read a JSON body, or a multipart one carrying files.

        With files, discord.py sends the JSON as a "payload_json" part and each file
        as "files[0]", "files[1]" and so on. The files are kept on the request.
        """
        request[UPLOADS] = {}
        if not request.can_read_body:
            return {}
        if request.content_type != "multipart/form-data":
            return await request.json()
        form = await request.post()
        body = json.loads(str(form["payload_json"]))
        for name, part in form.items():
            if isinstance(part, web.FileField):
                index = int(name.removeprefix("files[").removesuffix("]"))
                request[UPLOADS][index] = (part.filename, part.file.read())
        return body

    def _attachments_after(
        self, request: web.Request, listed: list[dict[str, Any]], current: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """A message's attachments after a create or edit, the way Discord's API v10 works them out.

        Each listed entry is either an attachment the message already has, by its
        ID, or a new upload, by its index among the uploaded files. Anything not
        listed is removed.
        """
        existing = {attachment["id"]: attachment for attachment in current}
        uploads: dict[int, tuple[str, bytes]] = request[UPLOADS]
        result = []
        for entry in listed:
            if str(entry["id"]) in existing:
                result.append(existing[str(entry["id"])])
                continue
            filename, data = uploads[int(entry["id"])]
            attachment_id = str(self.next_id())
            self.uploaded_files[attachment_id] = data
            url = f"https://cdn.fake/{attachment_id}/{filename}"
            result.append({"id": attachment_id, "filename": filename, "size": len(data), "url": url, "proxy_url": url})
        return result

    async def _dispatch(self, event: str, data: dict[str, Any]) -> None:
        assert self._ws is not None, "The bot isn't connected."
        await self._ws.send_json({"op": 0, "s": next(self._sequence), "t": event, "d": data})

    async def _gateway(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 45000}})
        async for raw in ws:
            payload = json.loads(raw.data)
            if payload["op"] == 1:
                await ws.send_json({"op": 11})
            elif payload["op"] == 2:
                self.identify = payload["d"]
                self.identify_count += 1
                await self._send_ready()
            elif payload["op"] == 4:
                await self._bot_voice_update(payload["d"])
        return ws

    async def _send_ready(self) -> None:
        bot_user = self.members[BOT_USER_ID]["user"]
        await self._dispatch(
            "READY",
            {
                "v": 10,
                "user": bot_user,
                "guilds": [{"id": str(GUILD_ID), "unavailable": True}],
                "session_id": "fake-session",
                "resume_gateway_url": self.gateway_url,
                "application": {"id": str(BOT_USER_ID), "flags": 0},
            },
        )
        await self._dispatch("GUILD_CREATE", self._guild_payload())

    async def _bot_voice_update(self, data: dict[str, Any]) -> None:
        self.bot_voice_flags = {"self_deaf": data["self_deaf"], "self_mute": data["self_mute"]}
        channel_id = int(data["channel_id"]) if data["channel_id"] else None
        if channel_id is None:
            self.bot_left_voice_itself = True
            self.voice.pop(BOT_USER_ID, None)
        else:
            self.voice[BOT_USER_ID] = channel_id
        await self._dispatch("VOICE_STATE_UPDATE", self._voice_state(BOT_USER_ID, channel_id))

    # ----- REST handlers -----------------------------------------------------

    async def _get_me(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        return _json(self.members[BOT_USER_ID]["user"])

    async def _get_application(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        return _json(
            {
                "id": str(BOT_USER_ID),
                "name": "TempVC",
                "description": "",
                "icon": None,
                "bot_public": False,
                "bot_require_code_grant": False,
                "owner": self._user_payload(1, "Owner", bot=False),
                "verify_key": "0" * 64,
                "flags": 0,
                "summary": "",
            }
        )

    async def _create_channel(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        if not self._base_permissions(BOT_USER_ID) & MANAGE_CHANNELS and not self._is_admin(BOT_USER_ID):
            return _error(403, 50013, "Missing Permissions")
        await asyncio.sleep(self.channel_create_delay)
        requested = body.get("permission_overwrites") or []
        for overwrite in requested:
            refusal = self._forbidden_overwrite(int(overwrite["allow"]), int(overwrite["deny"]))
            if refusal:
                return refusal
        parent_id = int(body["parent_id"]) if body.get("parent_id") else None
        # With no overwrites given, a new channel copies its category's (they are "synced").
        inherited = self.channels[parent_id]["permission_overwrites"] if parent_id else []
        overwrites = json.loads(json.dumps(requested or inherited))
        channel_id = self.add_channel(body["name"], parent_id=parent_id, overwrites=overwrites)
        channel = self.channels[channel_id]
        await self._dispatch("CHANNEL_CREATE", channel)
        return _json(channel)

    async def _edit_member(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        member_id = int(request.match_info["member_id"])
        if "channel_id" not in body:
            return _json(self.members[member_id])
        channel_id = int(body["channel_id"])
        permissions = self.effective_permissions(channel_id, BOT_USER_ID)
        # "the API user must have permissions to both connect to the channel and have MOVE_MEMBERS"
        if not permissions & CONNECT or not permissions & MOVE_MEMBERS:
            return _error(403, 50013, "Missing Permissions")
        if member_id not in self.voice or member_id in self.refuse_to_move:
            return _error(400, 40032, "Target user is not connected to voice.")
        await self.join_voice(member_id, channel_id)
        return _json(self.members[member_id])

    def _bot_can_manage_permissions(self, channel_id: int) -> bool:
        return bool(self.effective_permissions(channel_id, BOT_USER_ID) & MANAGE_ROLES)

    async def _set_overwrite(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        channel_id = int(request.match_info["channel_id"])
        target_id = request.match_info["target_id"]
        if channel_id not in self.channels:
            return _error(404, 10003, "Unknown Channel")
        if not self._bot_can_manage_permissions(channel_id):
            return _error(403, 50013, "Missing Permissions")
        allow, deny = int(body.get("allow", 0)), int(body.get("deny", 0))
        refusal = self._forbidden_overwrite(allow, deny)
        if refusal:
            return refusal
        channel = self.channels[channel_id]
        others = [o for o in channel["permission_overwrites"] if o["id"] != target_id]
        channel["permission_overwrites"] = [*others, {"id": target_id, "type": body["type"], "allow": str(allow), "deny": str(deny)}]
        await self._dispatch("CHANNEL_UPDATE", channel)
        return web.Response(status=204)

    async def _delete_overwrite(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        channel_id = int(request.match_info["channel_id"])
        if not self._bot_can_manage_permissions(channel_id):
            return _error(403, 50013, "Missing Permissions")
        channel = self.channels[channel_id]
        target_id = request.match_info["target_id"]
        channel["permission_overwrites"] = [o for o in channel["permission_overwrites"] if o["id"] != target_id]
        await self._dispatch("CHANNEL_UPDATE", channel)
        return web.Response(status=204)

    async def _edit_channel(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        channel_id = int(request.match_info["channel_id"])
        if channel_id not in self.channels:
            return _error(404, 10003, "Unknown Channel")
        if not self.effective_permissions(channel_id, BOT_USER_ID) & MANAGE_CHANNELS:
            return _error(403, 50013, "Missing Permissions")
        headers: dict[str, str] = {}
        if "name" in body:
            limited = self._check_rename_limit(channel_id, headers)
            if limited:
                return limited
            if not 1 <= len(body["name"]) <= 100 or "banned-word" in body["name"]:
                return _error(400, 50035, "Invalid Form Body")
        if "user_limit" in body and not 0 <= body["user_limit"] <= 99:
            return _error(400, 50035, "Invalid Form Body")
        channel = self.channels[channel_id]
        channel.update({key: body[key] for key in ("name", "user_limit") if key in body})
        await self._dispatch("CHANNEL_UPDATE", channel)
        return _json(channel, headers=headers)

    def _check_rename_limit(self, channel_id: int, headers: dict[str, str]) -> web.Response | None:
        """Allow two renames per channel, then answer the way Discord does."""
        if len(self.renames[channel_id]) >= RENAMES_ALLOWED:
            return _json(
                {"message": "You are being rate limited.", "retry_after": RENAME_RETRY_AFTER_SECONDS, "global": False},
                status=429,
                # discord.py treats a 429 without "Via" as a Cloudflare ban.
                headers={"Via": "1.1 google", "Retry-After": str(int(RENAME_RETRY_AFTER_SECONDS))},
            )
        self.renames[channel_id].append(time.monotonic())
        if self.report_exhausted_rename_bucket and len(self.renames[channel_id]) == RENAMES_ALLOWED:
            headers.update(
                {
                    "X-RateLimit-Limit": str(RENAMES_ALLOWED),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset-After": str(RENAME_RETRY_AFTER_SECONDS),
                    "X-RateLimit-Bucket": "rename-bucket",
                }
            )
        return None

    async def _delete_channel(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        channel_id = int(request.match_info["channel_id"])
        if channel_id not in self.channels:
            return _error(404, 10003, "Unknown Channel")
        if not self.effective_permissions(channel_id, BOT_USER_ID) & MANAGE_CHANNELS:
            return _error(403, 50013, "Missing Permissions")
        channel = await self._remove_channel(channel_id)
        return _json(channel)

    async def _remove_channel(self, channel_id: int) -> dict[str, Any]:
        """Deleting a voice channel disconnects everyone who is in it."""
        for member_id in self.people_in(channel_id):
            await self.leave_voice(member_id)
        channel = self.channels.pop(channel_id)
        await self._dispatch("CHANNEL_DELETE", channel)
        return channel

    def _message_payload(self, channel_id: int, body: dict[str, Any], *, author_id: int = BOT_USER_ID) -> dict[str, Any]:
        return {
            "id": str(self.next_id()),
            "channel_id": str(channel_id),
            "guild_id": str(GUILD_ID),
            "author": self.members[author_id]["user"],
            "content": body.get("content") or "",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "edited_timestamp": None,
            "tts": False,
            "mention_everyone": False,
            "mentions": [],
            "mention_roles": [],
            "attachments": [],
            "embeds": body.get("embeds") or [],
            "components": body.get("components") or [],
            "pinned": False,
            "type": 0,
            "flags": body.get("flags", 0),
            "allowed_mentions_sent": body.get("allowed_mentions"),
        }

    async def _create_message(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        channel_id = int(request.match_info["channel_id"])
        if channel_id not in self.channels:
            return _error(404, 10003, "Unknown Channel")
        permissions = self.effective_permissions(channel_id, BOT_USER_ID)
        if not permissions & SEND_MESSAGES or (body.get("embeds") and not permissions & EMBED_LINKS):
            return _error(403, 50013, "Missing Permissions")
        if request[UPLOADS] and not permissions & ATTACH_FILES:
            return _error(403, 50013, "Missing Permissions")
        message = self._message_payload(channel_id, body)
        message["attachments"] = self._attachments_after(request, body.get("attachments") or [], [])
        await self._add_message(message)
        return _json(message)

    async def _add_message(self, message: dict[str, Any]) -> None:
        """Store a new message and report it to the bot, as Discord does for every message."""
        self.messages[int(message["id"])] = message
        self.channels[int(message["channel_id"])]["last_message_id"] = message["id"]
        await self._dispatch("MESSAGE_CREATE", message)

    def _apply_edit(self, message: dict[str, Any], body: dict[str, Any]) -> None:
        for key in ("content", "embeds", "components"):
            if key in body:
                message[key] = body[key] if body[key] is not None else ([] if key != "content" else "")
        message["edited_timestamp"] = "2026-01-01T00:00:01+00:00"

    async def _edit_message(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        message = self.messages.get(int(request.match_info["message_id"]))
        if message is None or int(message["channel_id"]) not in self.channels:
            return _error(404, 10008, "Unknown Message")
        if request[UPLOADS] and not self.effective_permissions(int(message["channel_id"]), BOT_USER_ID) & ATTACH_FILES:
            return _error(403, 50013, "Missing Permissions")
        self._apply_edit(message, body)
        if "attachments" in body:
            message["attachments"] = self._attachments_after(request, body["attachments"], message["attachments"])
        return _json(message)

    async def _delete_message(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        if self.messages.pop(int(request.match_info["message_id"]), None) is None:
            return _error(404, 10008, "Unknown Message")
        return web.Response(status=204)

    async def _interaction_callback(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        record = self.interactions[request.match_info["token"]]
        if record.acknowledged:
            return _error(400, 40060, "Interaction has already been acknowledged.")
        record.responses.append(body)
        data = body.get("data") or {}
        clicked = self._interaction_message(record.message_id)
        if body["type"] == 7 and clicked is not None:  # Update the clicked message.
            self._apply_edit(clicked, data)
        reply_id = self.next_id()
        if body["type"] == 4:  # A new message in reply. Ephemeral ones send no MESSAGE_CREATE.
            record.reply = {**self._message_payload(record.channel_id, data), "id": str(reply_id)}
        return _json(
            {
                "interaction": {
                    "id": str(record.id),
                    "type": 3,
                    "response_message_id": str(reply_id),
                    "response_message_loading": body["type"] == 5,
                    "response_message_ephemeral": bool(data.get("flags", 0) & EPHEMERAL_FLAG),
                }
            }
        )

    async def _interaction_followup(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        record = self.interactions[request.match_info["token"]]
        # discord.py sends "ephemeral" as a flag on the follow-up, and Discord also
        # makes it private if the defer was private.
        deferred_privately = any(r["type"] == 5 and r.get("data", {}).get("flags", 0) & EPHEMERAL_FLAG for r in record.responses)
        reply = dict(body)
        if deferred_privately:
            reply["flags"] = reply.get("flags", 0) | EPHEMERAL_FLAG
        record.followups.append(reply)
        channel_id = next(iter(self.channels))
        return _json(self._message_payload(channel_id, reply))

    async def _interaction_edit_original(self, request: web.Request, body: dict[str, Any]) -> web.Response:
        record = self.interactions[request.match_info["token"]]
        # "Original" means the bot's reply if it posted one, otherwise the clicked message.
        message = record.reply or self._interaction_message(record.message_id)
        if message is None:
            return _error(404, 10008, "Unknown Message")
        self._apply_edit(message, body)
        return _json(message)

    def _interaction_message(self, message_id: int | None) -> dict[str, Any] | None:
        """A channel message, or a reply the bot posted to an earlier click."""
        if message_id in self.messages:
            return self.messages[message_id]
        replies = (record.reply for record in self.interactions.values() if record.reply is not None)
        return next((reply for reply in replies if int(reply["id"]) == message_id), None)


def _json(data: Any, *, status: int = 200, headers: dict[str, str] | None = None) -> web.Response:
    """A JSON response with Discord's exact Content-Type, which discord.py checks for."""
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    return web.Response(body=json.dumps(data).encode(), status=status, headers=all_headers)


def _error(status: int, code: int, message: str) -> web.Response:
    return _json({"code": code, "message": message}, status=status)


def _find_all(tree: Any, key: str, value: Any) -> list[dict[str, Any]]:
    """Find every nested dictionary in `tree` where `key` equals `value`."""
    found: list[dict[str, Any]] = []
    if isinstance(tree, dict):
        if tree.get(key) == value:
            found.append(tree)
        for child in tree.values():
            found += _find_all(child, key, value)
    elif isinstance(tree, list):
        for child in tree:
            found += _find_all(child, key, value)
    return found


def find_component(message: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Find one button or dropdown in a message."""
    return _find_all(message, key, value)[0]
