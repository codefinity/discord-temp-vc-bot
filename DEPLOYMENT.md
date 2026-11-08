# Deployment guide

This guide takes you from nothing to a working bot on your own computer, with no coding. It takes about 30 minutes. To keep the bot running when your computer is off, follow [HOSTING.md](HOSTING.md) afterwards.

**You'll need:**

- A Discord account with the **Manage Server** permission on your server.
- A Windows, Mac or Linux computer.
- This project's folder on that computer.
- A friend or a second Discord account, for testing.

## Step 1: Install uv

uv is the tool that installs and runs the bot. It also downloads the right version of Python for you, so you don't need to install Python yourself.

1. Open a terminal:
   - **Windows:** right-click the Start button and choose **Terminal** (or **Windows PowerShell**).
   - **Mac:** open **Terminal** from Applications, then Utilities.
   - **Linux:** open your terminal app.
2. Paste the command for your system and press Enter.

   **Windows:**
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   **Mac or Linux:**
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Close the terminal and open a new one, so it can find uv.
4. Check that it worked:
   ```
   uv --version
   ```
   You should see a version number.

## Step 2: Create the bot in Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **New Application**, give it a name such as "Temp VC Bot", accept the terms, and click **Create**.
3. On the **General Information** page, copy the **Application ID** and save it somewhere. You'll need it in Step 3.
4. Open the **Bot** page in the left menu.
5. Under **Token**, click **Reset Token** and confirm. Copy the token and save it somewhere safe. You'll need it in Step 5, and Discord won't show it again.

   > **Treat the token like a password.** Anyone who has it can control your bot. Never share it or post it anywhere. If it ever leaks, click **Reset Token** again and update your `.env` file.

6. Still on the **Bot** page, find **Privileged Gateway Intents** and leave all three switches **off**. This bot only needs the server, voice and message events that Discord allows by default. It sees that a message was posted, but not what it says.

**Optional: stop other people adding your bot.** This is recommended, but the bot ignores other servers anyway. Do these in this order, because Discord shows an error if you do them the other way round:

1. On the **Installation** page, set **Install Link** to **None** and save.
2. On the **Bot** page, turn off **Public Bot** and save.

## Step 3: Invite the bot to your server

1. Copy this link into a text editor, and replace `YOUR_APPLICATION_ID` with the ID from Step 2:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&permissions=286313488&scope=bot
   ```
2. Open the edited link in your browser.
3. Choose your server and click **Continue**.
4. Keep every permission ticked, then click **Authorize**.

The number in the link, `286313488`, stands for exactly these eight permissions:

| Permission | Why the bot needs it |
|---|---|
| **View Channels** | To see the hub, the category and the temporary channels. |
| **Manage Channels** | To create, rename, set limits on and delete temporary channels. |
| **Manage Roles** | To control who can connect, which is how Lock works. On a channel, Discord calls this same permission "Manage Permissions". The bot never edits your server's roles. |
| **Connect** | To join temporary channels. Discord also won't let anyone edit a voice channel they can't connect to. |
| **Move Members** | To move people from the hub into their channel. |
| **Send Messages** | To post the control panel, timer and extension question. |
| **Embed Links** | To show the control panel and timer as embeds. |
| **Attach Files** | To show the circular countdown, which is a picture attached to the timer. |

**Invited the bot before the circular countdown was added?** Open the new link once more to add Attach Files. You don't need to remove the bot first.

The bot doesn't need Administrator. Please don't give it that, since the bot works without it.

## Step 4: Create the hub and the category

1. In your Discord server, right-click the channel list and choose **Create Category**. Name it something like "Temp VCs".
2. Create a voice channel named **Create Your Own VC**. This is the hub. It can go inside the new category or anywhere else.

**Three rules for the category:**

- Leave its permissions at the defaults, unless you want temporary channels to be private. New channels copy the category's permissions.
- Don't give any role an explicit **Allow** on Connect there. Members with that role could join locked channels.
- It's fine to keep permanent channels in it, because the bot only deletes channels it created.

## Step 5: Copy the IDs and fill in `.env`

**Turn on Developer Mode,** which adds "Copy ID" options to Discord:

1. Click the gear icon next to your name at the bottom left, to open **User Settings**.
2. Go to **Advanced** and turn on **Developer Mode**.

**Copy three IDs,** and save them somewhere:

1. Right-click your **server's icon** and choose **Copy Server ID**.
2. Right-click the **Create Your Own VC** channel and choose **Copy Channel ID**.
3. Right-click the **category's name** and choose the **Copy ID** option.

**Create your settings file:**

1. Open a terminal in the project folder:
   - **Windows:** open the `temp-vc-bot` folder in File Explorer, right-click an empty space and choose **Open in Terminal**.
   - **Mac or Linux:** type `cd ` with a space after it, drag the folder into the terminal, and press Enter.
2. Make your own copy of the example file, and open it:

   **Windows:**
   ```
   copy .env.example .env
   notepad .env
   ```

   **Mac or Linux:**
   ```
   cp .env.example .env
   nano .env
   ```
3. Replace each value. It should look like this, with your own values:
   ```
   DISCORD_TOKEN=your-long-token-from-step-2
   GUILD_ID=123456789012345678
   HUB_CHANNEL_ID=234567890123456789
   TEMP_VC_CATEGORY_ID=345678901234567890
   ```
4. Save and close the file. In Notepad, use **File**, then **Save**, and don't choose "Save as". The name must stay exactly `.env`, not `.env.txt`.

## Step 6: Start the bot

In the same terminal, in the project folder, run:

```
uv run temp-vc-bot
```

The first run takes a minute or so, while uv downloads Python and everything else the bot needs. When it's ready, you'll see a line like this:

```
2026-11-05 21:00:00,000 INFO     temp_vc_bot.cogs.voice_events: Logged in as Temp VC Bot.
```

Your bot now shows as online in Discord. Join **Create Your Own VC** to try it. To stop the bot, click the terminal and press **Ctrl+C**.

The bot only runs while this terminal is open. For 24/7 running, see [HOSTING.md](HOSTING.md).

## How members use the bot

You can share this section with your server.

**Getting a channel:** click **Create Your Own VC**. You'll be moved into your own channel, named after you, within a moment. You are its owner. If you already have a channel, clicking the hub takes you back to it, even if it's full or locked.

**Finding the controls:** open your channel's text chat using the chat icon on the voice channel. The bot tags you there, with a control panel. Anyone in the chat can see it, but only you can use it. Anyone else who clicks gets a private note: "Only the channel owner can do this."

| Control | What it does |
|---|---|
| **Rename** | Opens a form for a new name, of 1 to 100 characters. Discord allows about 2 renames per 10 minutes on each channel. If you reach that limit, the bot tells you when to try again. |
| **User limit** | Opens a form for a number from 0 to 99, where 0 means no limit. The number counts people only, because the bot adds a seat for itself. |
| **Locked / Unlocked** | Shows the current state, and switches it when clicked. While locked, new people can't join, but you can still get back in and people already inside stay. |
| **Set timer length** | Choose 20, 25, 30, 35 or 40 minutes. The default is 20. You can only change this before the timer starts. |

**The timer:**

- The timer message says "Waiting for someone to join…", with a full grey ring, until someone other than you joins. Bots don't count.
- Once someone joins, the countdown starts and the dropdown is disabled. The ring turns green and empties clockwise as time passes, with the minutes left in the middle. It updates once a minute, and the text above it shows the time the channel closes.
- While you're being asked to extend, an orange ring counts down the 2 minutes you have to answer. If you say Yes, a blue ring counts down the extra time.
- The countdown keeps going even if people leave.
- The timer stays at the bottom of the chat. A few seconds after anyone posts, the bot moves it back below the new messages.

**When time runs out,** the bot tags you and asks "Time's up! Extend by 10 minutes?":

- **Yes** adds 10 minutes, once only. When they end, the channel closes without asking again. The 10 minutes count from when the time ran out, so answering quickly gives you the most time.
- **No** closes the channel.
- **No answer within 2 minutes** closes the channel.

**When a channel closes,** everyone in it is disconnected and the channel is deleted. This also happens the moment the last person leaves, whatever the timer says.

## Test checklist

You need a friend or a second account for this. Keep the terminal open, so you can see any errors.

**To avoid waiting 20 minutes,** speed up the timers while testing:

1. Open `src/temp_vc_bot/constants.py` in a text editor.
2. Change `SECONDS_PER_MINUTE = 60` to `SECONDS_PER_MINUTE = 6`, and save.
3. Restart the bot. Everything now runs ten times faster: the 20-minute timer takes 2 minutes, the 2-minute answer window takes 12 seconds, and the extra 10 minutes take 1 minute. The messages and the ring still say the normal numbers.
4. **Change it back to 60 when you've finished, and restart the bot.**

**Creating a channel**

- [ ] Join the hub. You're moved into a new channel named after you, in the right category.
- [ ] The bot appears in your channel, shown as deafened.
- [ ] The control panel and a timer saying "Waiting for someone to join…", with a full grey ring, appear in the text chat.
- [ ] Join the hub again. You go back to the same channel, and no new one appears.
- [ ] With the bot still in your channel, have your friend join the hub. They get their own channel, the bot stays in yours, and their buttons still work.

**Controls**

- [ ] **Rename:** rename the channel. The name changes.
- [ ] **Rename limit:** rename it two more times quickly. The third rename tells you to try again later.
- [ ] **Limit:** set a limit of 2. The channel shows 3 seats, one being the bot's. You and one friend can join, and a third person can't.
- [ ] **Limit:** type `abc`. You get a private error message.
- [ ] **Lock:** click the lock button. The label changes to Locked. Your friend can't join from outside, but you can leave and come back through the hub.
- [ ] **Unlock:** click it again. Your friend can join.
- [ ] **Owner only:** have your friend click any button. They get the private "Only the channel owner can do this."

**Timer**

- [ ] **Set timer:** choose 25 minutes. The panel shows 25.
- [ ] **Timer start:** have your friend join. The ring turns green, showing 25 min left, and the dropdown becomes disabled.
- [ ] **Ring moves:** wait a little. The ring empties clockwise and the minutes in the middle go down.
- [ ] **Stays in view:** send a few messages in the channel's chat. About 5 seconds after the last one, the timer moves back to the bottom, and only one timer is left.
- [ ] **Keeps running:** have your friend leave. The countdown continues.

**Extension** (you'll need to run the timer three times)

- [ ] **Yes:** when time runs out, the bot tags you and asks, and the ring turns orange. Your friend clicking gets the private note. You click Yes, and the countdown updates with a blue ring. When it ends, the channel is deleted without asking again.
- [ ] **No:** run it again and click No. The channel is deleted.
- [ ] **No answer:** run it again and don't click. The channel is deleted when the answer window ends.

**Cleanup**

- [ ] **Empty channel:** create a channel and leave it. It's deleted right away, and the bot leaves voice.
- [ ] **Restart:** create a channel, press Ctrl+C in the terminal, and start the bot again. The old channel is deleted, and the hub is still there.

## Troubleshooting

The terminal is the first place to look, because the bot logs clear messages about most problems. On a server, see "View the logs" in [HOSTING.md](HOSTING.md).

### The bot won't start

| What you see | Cause | Fix |
|---|---|---|
| `Discord rejected DISCORD_TOKEN` | The token is wrong or has been reset. | On the **Bot** page in the Developer Portal, click **Reset Token**, then paste the new one into `.env`. Check there are no extra spaces or quote marks. |
| `Please fix these settings in .env` | A setting is missing or wrong. The message lists each one. | Fix what it lists. An ID must be only digits. |
| `No .env file found` | The file is missing, or was saved as `.env.txt`, or you're in the wrong folder. | Run the command from the `temp-vc-bot` folder. Run `dir` (Windows) or `ls -a` (Mac or Linux) to see the file's real name. |
| `uv` is not recognized | The terminal started before uv was installed. | Close the terminal and open a new one. |

### The bot is online, but joining the hub does nothing

Check the terminal for these messages:

| Message | Fix |
|---|---|
| `not in the server with GUILD_ID` | `GUILD_ID` is wrong, or the bot hasn't been invited. Check Step 3 and Step 5. |
| `HUB_CHANNEL_ID ... is not a voice channel` | That ID is wrong. Copy the hub's ID again. |
| `TEMP_VC_CATEGORY_ID ... is not a category` | That ID is wrong. Copy the category's ID again. |

If there's no message at all, make sure you restarted the bot after editing `.env`.

### Missing permissions

- **The log names them.** `The bot is missing these permissions in the category: ...` lists exactly what's missing. Use the invite link from Step 3 again to fix them, and keep every permission ticked. You don't need to remove the bot first.
- **A category or channel can override the server's settings.** If the log still shows missing permissions after that, open the category's **Edit Category**, then **Permissions**, and check that nothing denies those permissions to the bot's role or to `@everyone`.
- **Your server requires two-factor authentication (2FA) for moderator actions?** Then [the account that owns the bot needs 2FA turned on too](https://docs.discord.com/developers/topics/permissions), or Discord refuses the Manage Channels and Manage Roles actions.

### The bot can't join voice

The channel still works without the bot in it, so this isn't urgent. The log says `Could not join voice channel` and gives a reason.

- **Is the bot already in another temp VC?** That's normal, not an error. A bot can only be in one voice channel per server. See the [README](README.md#2-a-bot-can-only-be-in-one-voice-channel-per-server).
- **Check the voice libraries.** Run this command:
  ```
  uv run python -m discord --version
  ```
  It should list `davey`, which Discord's required voice encryption depends on. If the error mentions `4017` or `davey`, or a newer `davey` may help, run:
  ```
  uv lock --upgrade-package discord-py --upgrade-package davey
  uv sync
  ```
- **A timeout?** Your network may block the voice connection, which uses UDP. This is common on school and office networks. Try another network or a server.

### Other problems

| What you see | Cause | Fix |
|---|---|---|
| "This interaction failed" when clicking a button | The bot was stopped or restarted, or the channel just closed. | Check the bot is running. Rejoin the hub for a new channel. |
| The bot says it can't find the category | The category was deleted. | Create a new one and update `TEMP_VC_CATEGORY_ID`. |
| A new channel can't be created, and a notice appears in the hub | The category is full (Discord allows 50 channels in one) or the server is full (500). | Delete some channels. |
| The countdown shows the wrong time | The computer's clock is wrong. | Fix the clock. On a server, see [HOSTING.md](HOSTING.md). |
| The ring looks blurred for a moment when it first appears or changes | Discord shows a blurred preview of every new picture until it has loaded. A slow connection makes it last longer. | Nothing to fix: it sharpens once loaded. The ring changes once a minute to keep this rare. |
| The timer stays above new messages instead of moving down | The bot can't post in the channel. The log says `Could not move the timer down`. | Open the invite link from Step 3 again. |
| The timer shows text but no ring | The bot is missing Attach Files (the log names it at startup), or drawing failed (the log says `Could not draw the countdown ring`). | Open the invite link from Step 3 again. For a drawing error, check `uv sync` finished without errors. |
| Timers run far too fast | `SECONDS_PER_MINUTE` is still set for testing. | Set it back to 60 in `constants.py` and restart. |
| Channels get deleted right after they're created, or everything happens twice | Two copies of the bot are running, perhaps one on your computer and one on a server. Each sees the other's channels as leftovers. | Stop one of them. |
| `warning: VIRTUAL_ENV=... does not match` | You have Anaconda or another Python tool active. | This is harmless, so you can ignore it. |
