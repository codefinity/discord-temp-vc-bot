# Temporary Voice Channel Bot

A Discord bot that gives each member their own voice channel on a timer.

A member joins the **Create Your Own VC** voice channel (the "hub"). The bot then creates a voice channel for them, moves them in, and posts a control panel in that channel's text chat. It also asks the owner to give the channel a name and a status: if they don't within 2 minutes, the channel is deleted. While they own it, the owner has a 👑 in front of their name. Once someone else joins, a countdown starts. When time runs out, the owner can extend once, and then the channel is deleted. If everyone leaves first, it's deleted right away. The owner can also end it early from the control panel. The bot manages each channel through its permissions. It only joins one for a few seconds, to play a /timer's beeps and ring.

Members can also find a group first. In the topic channel, such as **#find-your-group**, **/topic** asks for a channel name, a status and what they want to discuss, and shows them a card only they can see. **Advertise** posts the card for everyone, and others click **I'm interested**. When the member clicks **Create a VC**, the bot creates the channel and pings everyone who's interested with a link to join it.

In a temp VC's chat, anyone in the channel can type **/timer 25** for a 25-minute countdown with **Start**, **Pause** and **Stop** buttons. Start counts down 3, 2, 1 with a beep on each, and the bot rings for 3 seconds when time is up. It's only for the people in the channel, and doesn't change when the channel closes.

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
│   ├── sounds.py               Makes /timer's beeps and ring (added)
│   ├── ratelimit_fix.py        Fixes a discord.py rate-limit bug (added)
│   ├── cogs/
│   │   ├── voice_events.py     Hub joins, people joining and leaving, startup
│   │   ├── topics.py           The /topic command (added)
│   │   └── user_timer.py       The /timer command (added)
│   ├── services/
│   │   ├── channel_manager.py  Create, delete, rename, status, lock, limit, owner's crown
│   │   ├── session_setup.py    Posts a new channel's messages, from the hub or /topic (added)
│   │   ├── naming.py           Deletes a channel that isn't named in time (added)
│   │   ├── timer.py            Countdown, extension, auto-close
│   │   ├── topics.py           A topic's interested list, its VC and the invites (added)
│   │   ├── user_timer.py       A /timer's count-in, countdown and ring (added)
│   │   └── speaker.py          Joins voice for a moment to play a sound (added)
│   └── views/
│       ├── owner_only.py       Shared owner-only check (added)
│       ├── name_prompt.py      "Name your channel" button (added)
│       ├── control_panel.py    Rename, limit, lock, timer and end controls
│       ├── modals.py           Name-and-status and user limit forms
│       ├── extend_prompt.py    Yes and No extension buttons
│       ├── end_confirm.py      "End now?" question before ending early (added)
│       ├── topic_card.py       What a topic card shows (added)
│       ├── topic_draft.py      The /topic form and the private card (added)
│       ├── topic_ad.py         The public card with I'm interested (added)
│       └── user_timer.py       A /timer's Start, Pause and Stop buttons (added)
└── tests/                      Automated tests (added)
    ├── fake_discord.py         A stand-in for Discord's servers
    ├── test_end_to_end.py      The real bot against the stand-in
    ├── test_topics.py          /topic against the stand-in
    ├── test_user_timer.py      /timer against the stand-in
    └── test_units.py           Small pieces on their own
```

**Nine additions to the suggested layout:**

- `errors.py`: the buttons, forms and event handlers all need the same friendly messages, so they live in one place.
- `countdown_ring.py`: Discord embeds can't draw shapes, so the circular countdown is a picture. This file only draws it. `timer.py` decides what it shows and when.
- `views/owner_only.py`: the control panel and the extension prompt share one owner check. It has to be written a particular way (see the comment in that file), so one copy is safer than two.
- `ratelimit_fix.py`: works around a discord.py bug that allowed only one new channel per day. See "Discord behaviour the code handles" below.
- `services/naming.py` and `views/name_prompt.py`: making the owner name each channel wasn't in the brief. The prompt and its button live in the view, and the 2-minute deadline lives in the service, next to the timer.
- `views/end_confirm.py`: the End discussion button wasn't in the brief. Ending a channel early can't be undone, so the button asks the owner to confirm first, and that question lives here.
- `/topic` wasn't in the brief either. The command is in `cogs/topics.py`, the work behind it in `services/topics.py`, and its cards in the three `views/topic_*.py` files. A topic's VC is set up the same way as one from the hub, so that step moved from `voice_events.py` into `services/session_setup.py`, which both use.
- `/timer` wasn't in the brief either. The command is in `cogs/user_timer.py`, the timer itself in `services/user_timer.py` and its buttons in `views/user_timer.py`. `sounds.py` makes the beeps and the ring in code, the way `countdown_ring.py` draws the ring, so there are no sound files. `services/speaker.py` plays them, and is the only part of the bot that joins voice.
- `tests/`: lets you check a change without a live server. The bot doesn't need these files to run.

## Where the brief and Discord disagree

The brief says to document any requirement that conflicts with how Discord works, and to build the closest version that works. There are three.

### 1. The bot can't give itself "Manage Permissions" in an overwrite

The brief asks for five permissions through overwrites, including Manage Permissions. [Discord's documentation](https://docs.discord.com/developers/resources/guild) says: *"Setting `MANAGE_ROLES` permission in channels is only possible for guild administrators."* Manage Permissions is that same permission, under the name Discord shows on a channel.

**What the bot does:** it writes the other four (View Channel, Connect, Manage Channel, Move Members) into each channel's overwrite. It gets Manage Permissions from the server-level **Manage Roles** permission in its invite link, which works the same on every channel. The bot can manage permissions on each temp VC, without being a server administrator.

If you do make the bot an Administrator, the code notices and writes the fifth one into the overwrite too.

### 2. A user limit of 99 can't get an extra seat

The brief asks for 0 to 99 people, plus a seat for the bot. This bot only joins voice for a few seconds, to play a /timer's sounds, so it needs no seat, but another bot in the channel, such as a music bot, still gets one. Discord's maximum is [99 seats](https://docs.discord.com/developers/resources/channel), so 99 people plus a bot would need 100.

**What the bot does:** it sets 99 seats, which leaves room for 98 people while a bot is inside, and tells the owner so. Every lower number works as the brief describes.

### 3. Some people can ignore a lock or a limit

Discord lets some people past these, and a bot can't change that:

- Server administrators and the server owner can always connect.
- Anyone with [Move Members](https://docs.discord.com/developers/topics/voice-connections) can join a full channel.
- Lock only denies `@everyone`, as the brief specifies. A role with an explicit **Allow** on Connect in the category still gets in. To avoid this, don't give roles an explicit Connect allow on that category.

## Discord behaviour the code handles

These don't conflict with the brief, but the bot would fail without handling them:

- **Voice needs `davey` and Opus.** Discord has [required end-to-end encryption (DAVE)](https://docs.discord.com/developers/topics/voice-connections) on voice channels since March 2026, and refuses other connections with close code 4017. Support arrived in discord.py 2.7.0, and the library needs the `davey` package to use it. The `discord.py[voice]` extra installs both `davey` and PyNaCl. Sending sound also needs the Opus library, which isn't a Python package. discord.py brings its own on Windows, and on Linux or a Mac it has to be installed. Without it, /timer works silently and the startup log says how to install it.
- **A bot can only be in one voice channel per server.** So /timer's sounds in different channels take turns. A channel waits up to 5 seconds (`SOUND_JOIN_SECONDS`) for its turn and for the bot to connect. After that, a count-in goes ahead silently and a ring is skipped. A sound is never worth holding up a timer for.
- **Leaving voice has to be confirmed before joining again.** Discord confirms a leave with an event. If that event arrives after the bot has joined another channel, it cuts off the new connection. discord.py's own voice client waits for the confirmation, and the stand-in in `tests/` does too, so the tests catch code that skips it.
- **Renames could hang for 10 minutes.** By default, discord.py quietly sleeps through a rate limit, so a rename would appear to do nothing. The bot sets `max_ratelimit_timeout`, which makes the library raise an error instead, and the owner sees "try again in about 9 minutes". Discord doesn't document the rename limit, so the bot relies on Discord's own answer and doesn't count renames itself.
- **That setting allowed only one new channel per day.** Discord counts new channels over 24 hours. With `max_ratelimit_timeout` set, discord.py refused every channel after the first until that window ended, even though Discord would have allowed more, and the log showed "Too many requests. Retry in 86251 seconds". [ratelimit_fix.py](src/temp_vc_bot/ratelimit_fix.py) lets requests through while Discord says some are left. When none are left, the member gets "try again later" in the hub straight away.
- **Setting a status without joining needs two permissions.** [Discord requires Manage Channels as well as Set Voice Channel Status](https://docs.discord.com/developers/resources/channel) from anyone not connected to the channel, which includes this bot nearly all the time. Its overwrite grants both. An overwrite can't grant a permission the bot lacks in the server, and Discord would refuse the whole overwrite, so a bot invited with an older link leaves Set Voice Channel Status out. Everything except the status keeps working, naming included, and the startup log says what's missing.
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
- The naming form also asks for a status, the line Discord shows under a voice channel's name, and won't submit without one. The control panel's **Rename** form has the same required box, filled in with the current status, so a status can be changed but not removed. A status of only spaces gets a message, like a name of only spaces.
- A status isn't a rename, so it doesn't use up one of the renames. The bot sets the status first. If Discord refuses it, the bot stops there: no rename is used up, and an unnamed channel stays unnamed, so the owner can try another. A rename that's refused or rate limited doesn't undo the status.
- If the status can't be set for a reason that's the bot's problem rather than the owner's, the name still counts. That's a missing Set Voice Channel Status permission or a Discord rate limit. Otherwise, on an older invite, every channel would be deleted for something its owner can't fix. The owner is told the status wasn't set.
- Discord doesn't include a channel's status with the channel, so the form shows the last status set through the bot. The bot only sends the status when the owner changes the box, so leaving it alone never overwrites a status someone in the channel set from Discord itself.

**Ownership**

- Ownership doesn't transfer. If the owner leaves while others stay, the channel keeps running and the owner can return through the hub. If they don't answer the prompt, the channel closes.
- The owner's only special powers are the panel, and being able to connect when the channel is locked or full (through the hub). They get no Discord permissions to edit the channel by hand.
- The owner gets a 👑 (`OWNER_CROWN`) in front of their nickname, so people can see who owns the channel. It shows wherever their name does on the server, not only in the channel, and stays while they're away from a channel others are still in. When the channel closes, the bot gives back their old nickname, unless they changed it in the meantime.
- Discord doesn't let the bot change the server owner's nickname, or anyone's whose highest role is level with or above the bot's. Those owners get no crown, and everything else works the same. The bot needs Manage Nicknames for the crown, and the startup log says if it's missing.
- Nicknames are at most 32 characters, so a long name is cut short after the crown.
- If the bot stops before a channel closes, the startup cleanup takes the crown off. It finds the owner through the leftover channel's permissions. The old nickname was lost in the restart, so the bot just removes the crown, or clears the nickname if what's left is the person's account name.

**Bots**

- The bot doesn't need to be in a channel to create, change or delete it, so it manages every temp VC through its permissions. It only joins one to play a /timer's beeps or ring, deafened, and leaves as soon as the sound ends. The rest of the time it isn't listed in any channel.
- Those visits take no seat. The bot has Move Members, so it can join a full channel anyway, and adding and removing a seat for every beep would use up channel edits. While it's in, for a few seconds, a channel with one seat left is full.
- Other bots, such as a music bot, count as non-humans. They don't start the timer or keep an empty channel open, and each one gets its own extra seat.

**Control panel**

- The panel and the timer are two separate messages, so each can update on its own.
- The panel message tags the owner, so they notice it. Anyone in the chat can see it.
- Besides the four permissions in its overwrite, the bot also gives itself Send Messages, Embed Links, Attach Files, Set Voice Channel Status and Speak on each channel, so a category's settings can't stop it posting the panel or the countdown ring, setting a status, or playing a /timer's sounds. Like the status, Speak is only given if the bot has it in the server, or Discord would refuse the whole overwrite.
- The lock button's label shows the state and the action, such as "Unlocked (click to lock)", so nobody reads "Locked" as a command.
- Setting a limit lower than the number of people inside removes nobody. It only stops new people joining.
- **End discussion** lets the owner delete the channel before time runs out. Ending can't be undone and disconnects everyone, so the button only asks a private "End now?" question first. An unanswered question expires after 60 seconds (`END_CONFIRM_SECONDS`) and leaves the channel alone. The button works in every phase, including before the timer starts.
- Names have spaces trimmed from both ends. Renaming a channel to its current name does nothing, so it doesn't use up a rename.

**Server setup**

- The bot serves the one server in `GUILD_ID` and ignores any others it's added to.
- The hub can be inside or outside the category.
- Startup cleanup deletes only channels that carry the bot's own Manage Channels overwrite. The hub and any permanent channels in the category are left alone. If you ever add a member-level overwrite for the bot to the category itself, synced channels would look like leftovers, so use a role instead.
- Errors that happen before a temp VC exists are posted in the hub's text chat for 30 seconds, tagging the member.

**/topic**

- `/topic` only works in the channel in `TOPIC_CHANNEL_ID`. Anywhere else, it privately says where to use it. Discord shows the command in every channel, and only server admins can hide it, under **Server Settings**, then **Integrations**.
- The command is registered only to the server in `GUILD_ID`, the one server the bot serves, each time the bot starts. If registering fails, the bot still runs without `/topic`, and the log says so.
- The name, status and topic are asked for in a form, because a card can't hold text boxes. All three are required. A name has 1 to 100 characters, a status up to 500 and a topic up to 1,000.
- The card from the form is private. Its **Edit** button reopens the form, filled in. Discord only lets the bot change a private card for 15 minutes after the last click, so the card expires after 14 minutes without one (`TOPIC_DRAFT_SECONDS`).
- **Create a VC** is on both cards, so a member can open a VC without advertising first. On the public card only the person who started the topic can use it. Anyone else who clicks gets a private note.
- **I'm interested** adds the clicker to the card's list, in the order people clicked. Clicking again takes them off. The person who started the topic is already in, so their click just gets a private note. The card lists 30 people by name (`MAX_INTERESTED_SHOWN`), then says how many more.
- A public card closes after 60 minutes with no clicks (`TOPIC_IDLE_MINUTES`). Its buttons are removed and it says so.
- "Invited" means a message in the topic channel that pings everyone interested and links to the VC. Clicking the link joins it. Discord can't move someone into a channel unless they're already in voice, and many members turn off direct messages from servers, so the bot doesn't send those. Each message pings at most 50 people (`INVITES_PER_MESSAGE`), so a big crowd gets more than one message.
- The VC is an ordinary temp VC with the topic's name and status: same category and permissions, control panel, timer, crown and cleanup. It skips the naming prompt, because it already has a name. Anyone can join, not only the people who were interested.
- If the member who clicks **Create a VC** is in a voice channel, they're moved into the new VC. If not, it waits for them or anyone else. If nobody joins within 5 minutes (`TOPIC_JOIN_DEADLINE_MINUTES`), it's deleted, because no "leave" event would ever clean it up. The member is told this when the VC is created.
- One person can own one temp VC at a time, from the hub or `/topic`. **Create a VC** tells someone who already owns one to end it first.
- Topics live in memory, like sessions. After a restart, buttons on cards posted before it stop working, and members start a new topic.
- The topic channel's own topic, the line Discord shows at the top of a channel, explains `/topic` and asks people not to write anything else there (`TOPIC_CHANNEL_DESCRIPTION`). This can't be a private message to each person who opens the channel: Discord doesn't tell bots when someone opens a channel, and a bot can only show a message to one person as a reply to something that person clicked or typed. The topic line is shown to everyone, but it isn't a message in the chat.
- The bot sets the channel's topic on every start, but only if it's different, because Discord allows only a few channel edits every 10 minutes. A change made by hand in Discord is undone at the next start, so change the text in `constants.py` instead. It only asks: everyone can still post, and the bot deletes nobody's messages.
- Anyone who posts in the topic channel gets a reply that pings them and says the channel is only for `/topic` (`TOPIC_CHAT_REPLY_MESSAGE`). Admins do too. Bots, Discord's own notices such as "pinned a message", and messages in the channel's threads don't. A reply can't be private: only answers to a slash command, button or form can be. Everyone in the channel sees it, so the bot deletes it after 30 seconds (`TOPIC_CHAT_REPLY_SECONDS`).
- While someone's reply is showing, their next messages don't get another one, so posting ten messages in a row doesn't bring ten replies.
- The bot can't see what people write, because that needs the privileged Message Content intent, so every message gets the same reply. The reply shows `/topic` as a command mention, which people can click to fill in the command. The bot gets the command's ID when it registers it. If registering failed, the reply shows plain `/topic` text.

**/timer**

- `/timer` only works in a temp VC's chat. There the bot is sure to have the permissions it needs, and the timer goes when the channel does. Anywhere else, it privately says so.
- Anyone in that voice channel can set a timer and use its buttons, not only the owner. It's for the people in the call, like a kitchen timer. Anyone not in the channel gets a private note.
- A timer is 1 to 60 whole minutes (`MIN_USER_TIMER_MINUTES`, `MAX_USER_TIMER_MINUTES`). Like every other duration, it follows `SECONDS_PER_MINUTE` for testing.
- A channel has one timer at a time. One that has finished or been stopped doesn't count, and nor does one whose message someone deleted.
- It's separate from the channel's own timer. It doesn't make the channel last longer or close sooner.
- **Start** counts 3, 2, 1, a second apart (`COUNT_IN_FROM`, `COUNT_IN_BEAT_SECONDS`), with a beep on each and a higher beep as the timer starts. Carrying on after a pause counts in again, so everyone's ready.
- The count-in waits until the bot is in the channel, so the numbers and the beeps line up. That usually takes a second or two, and the timer says "get ready" meanwhile. The sound times its own beeps, and the numbers follow the same schedule, so a slow edit can't make them drift apart.
- **Pause** works during the count-in too. Before the timer has ever run, that takes it back to the start.
- When time is up, the bot rings for 3 seconds (`RING_SECONDS`). It starts joining 5 seconds before the end, so the ring is on time. The timer shows "time's up" on time even if joining is slow, because the ring runs alongside it.
- **Stop** doesn't ring: whoever clicked it already knows. It needs no confirming, unlike ending a channel, because a timer is easy to set again.
- When a timer finishes or stops, its buttons go, and its message shows the result.
- The ring shows the minutes and seconds left, rounded up, and is redrawn every 10 seconds (`USER_TIMER_REDRAW_SECONDS`). The redraws are counted back from the end, like the channel's ring, so the time is exact each time it's drawn. Every redraw is a new picture, with the same blurred moment as the channel's ring, so it isn't redrawn every second. The text above it shows the end time, which doesn't change, for the same reason as the channel's timer.
- Discord allows about 5 edits per 5 seconds in a channel. The count-in's 3, 2, 1 and the start use 4. The "get ready" that comes first is the bot's answer to the Start click itself, which doesn't count.
- The timer isn't moved to the bottom of the chat, unlike the channel's timer. The channel's timer moves below it when it's posted.
- The sounds are made in code (`sounds.py`) at 30% of full volume. People can turn the bot down in Discord, like anyone else.
- Timers live in memory, like sessions. A restart deletes every temp VC, and their timers with them.

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

**What the tests can't cover:** the stand-in has no audio connection, so the bot's real voice handshake, including DAVE, and what the beeps sound like can only be checked on a real server. Discord's real responses can also differ from the stand-in's. The [test checklist in DEPLOYMENT.md](DEPLOYMENT.md#test-checklist) covers what to try on a real server.
