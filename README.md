# Temporary Voice Channel Bot

A Discord bot that gives each member their own voice channel on a timer.

A member joins the **Create Your Own VC** voice channel (the "hub"). The bot then creates a voice channel for them, moves them in, and posts a control panel in that channel's text chat. It also asks the owner to name the channel: if they don't within 2 minutes, the channel is deleted. While they own it, the owner has a 👑 in front of their name. Once someone else joins, a countdown starts. When time runs out, the owner can extend once, and then the channel is deleted. If everyone leaves first, it's deleted right away. The owner can also end it early from the control panel. The bot never joins a voice channel itself. It manages each one through its permissions.

- **To set it up:** follow [DEPLOYMENT.md](DEPLOYMENT.md). It's written for non-developers.
- **To keep it running 24/7:** follow [HOSTING.md](HOSTING.md).

## How the code is organized

```
temp-vc-bot/
├── pyproject.toml              Project settings and dependencies
├── uv.lock                     Exact dependency versions
├── .env.example                Template for your settings
├── .gitignore                  Keeps .env out of git
├── README.md                   This file
├── DEPLOYMENT.md               Setup guide, usage and troubleshooting
├── HOSTING.md                  Host comparison and server setup
├── src/temp_vc_bot/
│   ├── main.py                 Entry point
│   ├── config.py               Loads and checks the .env settings
│   ├── constants.py            Every duration, limit and label
│   ├── bot.py                  Creates the bot, sets intents, loads the cog
│   ├── state.py                One channel's session, and the list of sessions
│   ├── errors.py               Friendly error messages (added)
│   ├── countdown_ring.py       Draws the circular countdown image (added)
│   ├── ratelimit_fix.py        Fixes a discord.py rate-limit bug (added)
│   ├── cogs/
│   │   └── voice_events.py     Hub joins, people joining and leaving, startup
│   ├── services/
│   │   ├── channel_manager.py  Create, delete, rename, lock, limit, owner's crown
│   │   ├── naming.py           Deletes a channel that isn't named in time (added)
│   │   └── timer.py            Countdown, extension, auto-close
│   └── views/
│       ├── owner_only.py       Shared owner-only check (added)
│       ├── name_prompt.py      "Name your channel" button (added)
│       ├── control_panel.py    Rename, limit, lock, timer and end controls
│       ├── modals.py           Rename and user limit forms
│       ├── extend_prompt.py    Yes and No extension buttons
│       └── end_confirm.py      "End now?" question before ending early (added)
└── tests/                      Automated tests (added)
    ├── fake_discord.py         A stand-in for Discord's servers
    ├── test_end_to_end.py      The real bot against the stand-in
    └── test_units.py           Small pieces on their own
```

**Seven additions to the suggested layout:**

- `errors.py`: the buttons, forms and event handlers all need the same friendly messages, so they live in one place.
- `countdown_ring.py`: Discord embeds can't draw shapes, so the circular countdown is a picture. This file only draws it. `timer.py` decides what it shows and when.
- `views/owner_only.py`: the control panel and the extension prompt share one owner check. It has to be written a particular way (see the comment in that file), so one copy is safer than two.
- `ratelimit_fix.py`: works around a discord.py bug that allowed only one new channel per day. See "Discord behaviour the code handles" below.
- `services/naming.py` and `views/name_prompt.py`: making the owner name each channel wasn't in the brief. The prompt and its button live in the view, and the 2-minute deadline lives in the service, next to the timer.
- `views/end_confirm.py`: the End discussion button wasn't in the brief. Ending a channel early can't be undone, so the button asks the owner to confirm first, and that question lives here.
- `tests/`: lets you check a change without a live server. The bot doesn't need these files to run.

## Where the brief and Discord disagree

The brief says to document any requirement that conflicts with how Discord works, and to build the closest version that works. There are three.

### 1. The bot can't give itself "Manage Permissions" in an overwrite

The brief asks for five permissions through overwrites, including Manage Permissions. [Discord's documentation](https://docs.discord.com/developers/resources/guild) says: *"Setting `MANAGE_ROLES` permission in channels is only possible for guild administrators."* Manage Permissions is that same permission, under the name Discord shows on a channel.

**What the bot does:** it writes the other four (View Channel, Connect, Manage Channel, Move Members) into each channel's overwrite. It gets Manage Permissions from the server-level **Manage Roles** permission in its invite link, which works the same on every channel. The bot can manage permissions on each temp VC, without being a server administrator.

If you do make the bot an Administrator, the code notices and writes the fifth one into the overwrite too.

### 2. A user limit of 99 can't get an extra seat

The brief asks for 0 to 99 people, plus a seat for the bot. This bot doesn't join voice, so it needs no seat, but another bot in the channel, such as a music bot, still gets one. Discord's maximum is [99 seats](https://docs.discord.com/developers/resources/channel), so 99 people plus a bot would need 100.

**What the bot does:** it sets 99 seats, which leaves room for 98 people while a bot is inside, and tells the owner so. Every lower number works as the brief describes.

### 3. Some people can ignore a lock or a limit

Discord lets some people past these, and a bot can't change that:

- Server administrators and the server owner can always connect.
- Anyone with [Move Members](https://docs.discord.com/developers/topics/voice-connections) can join a full channel.
- Lock only denies `@everyone`, as the brief specifies. A role with an explicit **Allow** on Connect in the category still gets in. To avoid this, don't give roles an explicit Connect allow on that category.

## Discord behaviour the code handles

These don't conflict with the brief, but the bot would fail without handling them:

- **Renames could hang for 10 minutes.** By default, discord.py quietly sleeps through a rate limit, so a rename would appear to do nothing. The bot sets `max_ratelimit_timeout`, which makes the library raise an error instead, and the owner sees "try again in about 9 minutes". Discord doesn't document the rename limit, so the bot relies on Discord's own answer and doesn't count renames itself.
- **That setting allowed only one new channel per day.** Discord counts new channels over 24 hours. With `max_ratelimit_timeout` set, discord.py refused every channel after the first until that window ended, even though Discord would have allowed more, and the log showed "Too many requests. Retry in 86251 seconds". [ratelimit_fix.py](src/temp_vc_bot/ratelimit_fix.py) lets requests through while Discord says some are left. When none are left, the member gets "try again later" in the hub straight away.
- **Losing Connect removes other permissions.** [Denying Connect also denies Manage Channel](https://docs.discord.com/developers/topics/permissions). The bot's own Connect overwrite is what lets it still manage and delete a locked channel.
- **A channel nobody entered gets no "leave" event.** If the owner leaves voice before the bot can move them in, the bot deletes the new channel at once. Otherwise it would stay forever.
- **The bot can reconnect at any time.** Discord may fire `on_ready` more than once, and discord.py then replaces its cached objects. So startup cleanup runs only once, sessions store IDs instead of objects, and the bot catches up on anything it missed while disconnected.

## Assumptions

The brief left these open. Each one is easy to change.

**Timer**

- The maximum lifetime of 50 minutes counts from when the timer starts. A channel with only its owner in it has no timer, because of the start condition, so it stays open for as long as they stay.
- The extra 10 minutes count from when the base time ended, not from when the owner clicked Yes. Otherwise, answering late would push the channel past 50 minutes. If the owner takes the full 2 minutes to answer, about 8 minutes remain.
- If the bot can't post the extension prompt, for example because it lost permission, that counts as no answer and the channel closes.
- The countdown is a ring that empties clockwise, with the minutes left in the middle. A fresh ring counts down each stretch: the base time (green), the 2-minute answer window (orange) and the extra time (blue).
- The ring is redrawn once a minute (`RING_REDRAW_MINUTES`), when the minutes in the middle change. Every redraw is a new picture, and Discord shows a blurred preview of a new picture until it has loaded, so redrawing more often means more blurred moments. The bot can't turn that preview off.
- The text above the ring shows the time the channel closes, in each person's own time zone, rather than a live countdown. Discord updates a live countdown every second, and that made the ring flicker. Now nothing in the timer message changes between the bot's once-a-minute redraws.
- The minutes in the ring are rounded up, so it shows 1 during the last minute and 0 only when time is up.
- If the ring can't be drawn, the timer carries on with text only, and the error is logged.
- The timer stays at the bottom of the chat. Discord can't keep a message on screen, and a pinned message only shows in the pins list. So when anything is posted below the timer, the bot waits 5 seconds (`TIMER_REPOST_DELAY_SECONDS`), posts the timer again at the bottom and deletes the old one. Everything posted during the wait is covered by one repost, so a busy chat doesn't flood the channel. This includes the bot's extension prompt, so the timer ends up just below it.
- To hear about new messages, the bot asks for Discord's message events. It never reads what people write, so it doesn't need the privileged Message Content intent.

**Naming**

- A new channel starts as "Alice's VC". A message between the control panel and the timer tags the owner with a **Name your channel** button, which opens a form with an empty, required name box. If the channel isn't named within 2 minutes (`NAME_DEADLINE_MINUTES`), it's deleted, even with people in it, and the owner is tagged in the hub to say why.
- Renaming from the control panel counts as naming too. So does choosing the name it already has, which doesn't use up a rename.
- Naming is a rename, so it uses one of Discord's roughly 2 renames per 10 minutes. After naming, the owner can rename once more in that time.
- A name Discord refuses doesn't count. The prompt stays, so the owner can try another.
- The prompt shows the deadline as a fixed time, not a live countdown, for the same reason as the timer.
- If the bot can't post the prompt, it doesn't delete the channel for being unnamed.

**Ownership**

- Ownership doesn't transfer. If the owner leaves while others stay, the channel keeps running and the owner can return through the hub. If they don't answer the prompt, the channel closes.
- The owner's only special powers are the panel, and being able to connect when the channel is locked or full (through the hub). They get no Discord permissions to edit the channel by hand.
- The owner gets a 👑 (`OWNER_CROWN`) in front of their nickname, so people can see who owns the channel. It shows wherever their name does on the server, not only in the channel, and stays while they're away from a channel others are still in. When the channel closes, the bot gives back their old nickname, unless they changed it in the meantime.
- Discord doesn't let the bot change the server owner's nickname, or anyone's whose highest role is level with or above the bot's. Those owners get no crown, and everything else works the same. The bot needs Manage Nicknames for the crown, and the startup log says if it's missing.
- Nicknames are at most 32 characters, so a long name is cut short after the crown.
- If the bot stops before a channel closes, the startup cleanup takes the crown off. It finds the owner through the leftover channel's permissions. The old nickname was lost in the restart, so the bot just removes the crown, or clears the nickname if what's left is the person's account name.

**Bots**

- The bot never joins voice channels. It doesn't need to be in a channel to create, change or delete it, so it manages every temp VC through its permissions. It isn't listed in any channel and uses no seat.
- Other bots, such as a music bot, count as non-humans. They don't start the timer or keep an empty channel open, and each one gets its own extra seat.

**Control panel**

- The panel and the timer are two separate messages, so each can update on its own.
- The panel message tags the owner, so they notice it. Anyone in the chat can see it.
- Besides the four permissions in its overwrite, the bot also gives itself Send Messages, Embed Links and Attach Files on each channel, so a category's settings can't stop it posting the panel or the countdown ring.
- The lock button's label shows the state and the action, such as "Unlocked (click to lock)", so nobody reads "Locked" as a command.
- Setting a limit lower than the number of people inside removes nobody. It only stops new people joining.
- **End discussion** lets the owner delete the channel before time runs out. Ending can't be undone and disconnects everyone, so the button only asks a private "End now?" question first. An unanswered question expires after 60 seconds (`END_CONFIRM_SECONDS`) and leaves the channel alone. The button works in every phase, including before the timer starts.
- Names have spaces trimmed from both ends. Renaming a channel to its current name does nothing, so it doesn't use up a rename.

**Server setup**

- The bot serves the one server in `GUILD_ID` and ignores any others it's added to.
- The hub can be inside or outside the category.
- Startup cleanup deletes only channels that carry the bot's own Manage Channels overwrite. The hub and any permanent channels in the category are left alone. If you ever add a member-level overwrite for the bot to the category itself, synced channels would look like leftovers, so use a role instead.
- Errors that happen before a temp VC exists are posted in the hub's text chat for 30 seconds, tagging the member.

**Restarts**

- A restart deletes every temp VC, even with people in them, as the brief specifies. Plan updates for a quiet moment.
- Anyone already in the hub when the bot starts gets a channel straight away. Discord sends no event for people already there, so otherwise they'd wait until they rejoined.

**Project**

- The project is in its own `temp-vc-bot/` folder, matching the layout in the brief.
- Python is pinned to 3.12, as the brief asks.
- `aiohttp` is listed as a direct dependency, because `errors.py` imports it, though discord.py would install it anyway.

## Running the tests

```
uv run pytest
```

The tests run the real bot and the real discord.py library against a local stand-in for Discord, over real HTTP and websockets. No token or internet connection is needed. The stand-in enforces the Discord rules above, so it refuses things that real Discord would refuse.

**What the tests can't cover:** Discord's real responses can differ from the stand-in's. The [test checklist in DEPLOYMENT.md](DEPLOYMENT.md#test-checklist) covers what to try on a real server.
