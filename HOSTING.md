# Hosting guide

This guide compares places to run the bot 24/7, recommends one, and walks through setting it up.

**Prices and terms were checked on 5 November 2026.** They change often, and several changed during 2026, so check the links in [Sources](#sources) before you pay.

## What the bot needs

- **It must run 24/7.** The bot is a long-lived process that holds a websocket connection open to Discord. When it stops, channels stop being created and timers stop running.
- **It serves no website.** It only makes outgoing connections, so it needs no domain, no web server and no open ports. It never joins voice, so it doesn't need UDP either.
- **It needs very little.** I measured about **75 MB of memory** on Linux: about 50 MB for the bot, and 25 MB for the `uv run` process that starts it. The bot spends nearly all its time waiting for events.

**What won't work:**

- **Hosts that put idle apps to sleep.** These usually decide an app is idle when it gets no incoming web traffic. A bot never gets any, so it would be put to sleep and go offline.
- **Serverless functions.** They run briefly for each request and then stop, so they can't hold a connection open.

## The options compared

| Host | Monthly price | Stays on 24/7? | Limits | Catches |
|---|---|---|---|---|
| **RackNerd** 1 GB VPS | **$1.83** ($21.99 billed yearly) | Yes | 1 vCPU, 1 GB RAM, 20 GB SSD, 3 TB transfer, 1 IPv4 | You pay for the year up front. It's a promotion "while available". The SLA promises 99.999% for network and power, with account credit as compensation, and gives no figure for the server itself. |
| **Fly.io** shared-cpu-1x | **$2.21** (its own "Discord bot" example: $2.19 plus 1 GB of data) | Yes | 256 MB RAM | No free tier. The trial is $1 of credit, and trial machines stop after 5 minutes. A card is required after that. You deploy with Docker and its own tool, so the steps below don't apply. |
| **Google Cloud** e2-micro | **About $3.65** (the server is free, its public IP isn't) | Yes | 0.25 vCPU sustained, 1 GB RAM, 30 GB standard disk, 1 GB of free data a month, 3 US regions | Widely described as free, but a public IPv4 costs $0.005 an hour with only 1 free hour a month. A billing account and card are required. The 90-day trial must be upgraded, or the server is stopped. The console's default disk isn't covered, which reportedly adds about $3. |
| **DigitalOcean** Basic | **$4.00** | Yes | 1 vCPU, 512 MiB RAM, 10 GiB SSD, 500 GiB transfer, 1 IPv4 | You pay by card or PayPal. The pricing page gives no uptime figure. Otherwise few surprises: billing is monthly, and new users get $5 of credit. |
| **Oracle Cloud** Always Free E2.1.Micro | **$0** | **Not reliably** | 1/8 OCPU, 1 GB RAM, 50 Mbps, up to 2 servers | Oracle may reclaim a server as idle if, over 7 days, its CPU stays under 20% at the 95th percentile and its network use stays under 20%. This bot would meet that test. You can only create it in your home region, and creation can fail with "out of host capacity". Third-party guides say a card is required. |
| **Northflank** Sandbox | **$0** | Claims "Always on, no sleeping" | Up to 2 services. Size isn't stated, only "Limited" | **Active security incident.** On 30 October 2026 it told all users to "assume all environment variables have been compromised", and that's where your bot token would live. Its status page still showed "Monitoring" on 5 November, with no root cause published. Its docs also say the free tier "should not be used for production", and its pages disagree on whether a card is needed. |

**Also checked, and ruled out:**

- **Railway** has a $0 plan with $1 of usage credit a month, which doesn't roll over. At $10 per GB of memory per month, this bot's 75 MB alone costs about $0.75, before CPU, so the margin is thin. Railway's pages don't say what happens when the credit runs out. The Hobby plan costs $5.
- **Render** has no free plan for background workers. Its free web services stop after 15 minutes with no incoming traffic and have a cap of 750 hours a month. Render's paid Starter price wasn't visible on the pages I could read. Third-party comparisons put it at about $7.
- **Koyeb's** free instance can't run as a worker, and it stops after 1 hour with no incoming traffic.
- **Hetzner** lists CX23 at €5.99 and CAX11 at €6.49 before VAT, but both show "currently unavailable" to new customers.
- **AWS** Lightsail costs $5 with a public IPv4. The AWS free plan is now credit-based, and the account closes after 6 months at most.
- **Vultr** isn't included, because its pricing page refused my automated request, so I couldn't confirm its prices.

## Recommendation: RackNerd

**Use the RackNerd 1 GB VPS at $21.99 a year.** It's the cheapest option I could confirm that reliably stays on.

- **It's the cheapest real option.** The two $0 options both have serious problems for this bot. Oracle's idle rule targets exactly this kind of quiet process, and Northflank's security incident is still open. Over a year, RackNerd costs $21.99, compared with about $26.52 for Fly.io, $43.74 for Google Cloud and $48 for DigitalOcean.
- **It's a real server you pay for.** There's no sleeping, no hour cap and no idle rule to work around.
- **It has plenty of room.** 1 GB of memory is over 10 times what the bot used in my test.
- **The price should hold.** RackNerd says these plans renew "at the same rate for as long as you decide to keep the service".
- **It's a normal Linux server.** That means the git and systemd steps below work as written.

**The downsides:**

- You pay for a year up front.
- It's a budget provider. I read its SLA, but I have no independent uptime figures for it.
- The offer is a promotion. If it's gone, the next plan on its [KVM VPS page](https://www.racknerd.com/kvm-vps) is $26.99 a year, with 512 MB of memory, which is still enough.

**If you'd rather pay monthly, or use a bigger company,** choose **DigitalOcean at $4 a month**. Everything from Step 2 below works the same on any Ubuntu server.

## Setting up the server

These steps use a RackNerd server, but only Step 1 is specific to it. Allow about 30 minutes. Finish [DEPLOYMENT.md](DEPLOYMENT.md) first, so you have your token and IDs and know the bot works.

You'll type commands into the server over SSH, a secure remote connection. Windows 10 and 11, macOS and Linux all include it.

### Step 1: Create the server

1. Go to the [RackNerd specials page](https://www.racknerd.com/BlackFriday) and order the **1 GB KVM VPS**.
2. Choose a location near you, and the newest **Ubuntu** LTS version offered. Debian works too.
3. After paying, you'll get the server's **IP address** and **root password**. RackNerd normally emails these, and they're also in its control panel.
4. Open a terminal on your computer and connect, using your IP address:
   ```
   ssh root@YOUR_SERVER_IP
   ```
   Type `yes` if asked about the server's fingerprint, then enter the password. Nothing appears as you type it, which is normal.
5. Change the password, and install updates and git:
   ```
   passwd
   apt update && apt upgrade -y
   apt install -y git
   ```
6. Create a separate user to run the bot, so it doesn't run with full control of the server:
   ```
   adduser --disabled-password --gecos "" botuser
   ```
7. Check the clock is correct, because the countdown relies on it:
   ```
   timedatectl
   ```
   Look for `System clock synchronized: yes`. If it says `no`, run `timedatectl set-ntp true`.

### Step 2: Install uv

1. Switch to the bot's user:
   ```
   su - botuser
   ```
2. Install uv:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Log out of this user and back in, so the system can find uv:
   ```
   exit
   su - botuser
   uv --version
   ```

### Step 3: Get the code onto the server with git

**First, put the code on GitHub from your own computer:**

1. Install [Git](https://git-scm.com/downloads) if you don't have it, and create a free [GitHub](https://github.com) account.
2. On GitHub, click **New repository**. Name it `temp-vc-bot`, choose **Private**, leave everything else unticked, and click **Create repository**.
3. In a terminal in your `temp-vc-bot` folder, tell git who you are and prepare the files:
   ```
   git config user.name "Your Name"
   git config user.email "you@example.com"
   git add .
   git status
   ```
4. **Check the list that `git status` shows. `.env` must not be in it.** If it is, stop and make sure `.gitignore` is in the folder. A token pushed to GitHub has to be treated as leaked.
5. Save and upload, using your GitHub username:
   ```
   git commit -m "First version of the bot"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/temp-vc-bot.git
   git push -u origin main
   ```

**Then, give the server read-only access.** Run these on the server, as `botuser`:

1. Create a key for the server, and show it:
   ```
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
   cat ~/.ssh/id_ed25519.pub
   ```
2. Copy the line it prints. On GitHub, open your repository, then **Settings**, **Deploy keys** and **Add deploy key**. Paste it in, give it a title such as "Bot server", leave **Allow write access** unticked, and click **Add key**. This key only opens this one repository.
3. Download the code, using your GitHub username. Type `yes` if asked about GitHub's fingerprint:
   ```
   git clone git@github.com:YOUR_USERNAME/temp-vc-bot.git
   cd temp-vc-bot
   ```

### Step 4: Set up `.env` safely

Still on the server, as `botuser`:

1. Create the file, and make it readable only by this user:
   ```
   cp .env.example .env
   chmod 600 .env
   ```
2. Open it and fill in your values, the same as in [DEPLOYMENT.md](DEPLOYMENT.md):
   ```
   nano .env
   ```
   Save with **Ctrl+O** then **Enter**, and exit with **Ctrl+X**.
3. Check that git will never upload it. This should print `.env`:
   ```
   git check-ignore .env
   ```
4. Stop the bot on your own computer now, if it's still running. If two copies run at once, they'll both act on every hub join.
5. Test it. The first run downloads Python and everything else:
   ```
   uv run --frozen --no-dev temp-vc-bot
   ```
   When you see `Logged in as`, join the hub in Discord to check it works, then press **Ctrl+C** to stop it.

### Step 5: Run it automatically with systemd

systemd is the part of Linux that keeps services running. This makes the bot start when the server boots and restart if it crashes.

1. Go back to the root user:
   ```
   exit
   ```
2. Create the service file:
   ```
   nano /etc/systemd/system/temp-vc-bot.service
   ```
3. Paste this in, then save and exit:
   ```ini
   [Unit]
   Description=Temporary voice channel Discord bot
   Wants=network-online.target
   After=network-online.target

   [Service]
   User=botuser
   WorkingDirectory=/home/botuser/temp-vc-bot
   ExecStart=/home/botuser/.local/bin/uv run --frozen --no-dev temp-vc-bot
   # Come back 10 seconds after any crash.
   Restart=always
   RestartSec=10
   # 78 means bad settings. Restarting can't fix those, so don't keep trying.
   RestartPreventExitStatus=78
   # Ask the bot to shut down cleanly, instead of killing it outright.
   KillSignal=SIGINT
   TimeoutStopSec=30
   NoNewPrivileges=true

   [Install]
   WantedBy=multi-user.target
   ```
4. Turn it on:
   ```
   systemctl daemon-reload
   systemctl enable --now temp-vc-bot
   ```
5. Check it's running. Look for `active (running)` in green, and press **q** to leave:
   ```
   systemctl status temp-vc-bot
   ```
6. **Prove it survives a reboot.** Run this, which will close your connection. Wait a minute, and check that the bot comes back online in Discord:
   ```
   reboot
   ```

### Step 6: View the logs

Connect with `ssh root@YOUR_SERVER_IP`, then:

| To see | Run |
|---|---|
| New log lines as they happen (**Ctrl+C** to stop) | `journalctl -u temp-vc-bot -f` |
| The last 100 lines | `journalctl -u temp-vc-bot -n 100 --no-pager` |
| Whether it's running | `systemctl status temp-vc-bot` |

**If the status shows `failed` with `status=78`,** the settings are wrong. The logs will say which one. Fix it with `nano /home/botuser/temp-vc-bot/.env`, then run `systemctl restart temp-vc-bot`.

For other errors, see [Troubleshooting in DEPLOYMENT.md](DEPLOYMENT.md#troubleshooting).

### Step 7: Deploy updates

A restart deletes every temporary channel, even with people in them, so pick a quiet moment.

1. **On your own computer,** upload your changes:
   ```
   git add .
   git status
   git commit -m "Describe your change"
   git push
   ```
2. **On the server,** as root, download the changes and restart:
   ```
   su - botuser -c "cd ~/temp-vc-bot && git pull && ~/.local/bin/uv sync --frozen --no-dev"
   systemctl restart temp-vc-bot
   ```
3. Check that it came back:
   ```
   systemctl status temp-vc-bot
   ```

**Updating the bot's libraries.** For the latest fixes, run this on your own computer, test the bot, and then deploy it as above:

```
uv lock --upgrade-package discord-py
uv sync
uv run pytest
```

## Keeping the server safe

- Use a long, unique root password, because servers on the internet get constant login attempts.
- Install security updates now and then with `apt update && apt upgrade -y`.
- If the token ever leaks, go to the **Bot** page in the Developer Portal, click **Reset Token**, update `.env`, and run `systemctl restart temp-vc-bot`.

## Sources

All read on 5 November 2026.

**RackNerd**
- [VPS specials: the 1 GB plan and its renewal terms](https://www.racknerd.com/BlackFriday)
- [KVM VPS plans](https://www.racknerd.com/kvm-vps)
- [Service Level Agreement](https://www.racknerd.com/service-level-agreement)

**Fly.io**
- [Pricing, including the Discord bot example](https://docs.fly.io/about/pricing)
- [Free trial](https://docs.fly.io/about/free-trial)

**Google Cloud**
- [Free Tier limits](https://docs.cloud.google.com/free/docs/free-cloud-features)
- [External IP prices and free allowance](https://cloud.google.com/vpc/network-pricing)
- [e2-micro specifications](https://docs.cloud.google.com/compute/docs/general-purpose-machines)
- [Report on the IP and default disk costs](https://github.com/robhunter/agentdeals/issues/2704) (third party, and the source of the $3 disk figure)

**DigitalOcean**
- [Droplet pricing](https://www.digitalocean.com/pricing/droplets)

**Oracle Cloud**
- [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [InfoQ on the June 2026 cut to the Arm allowance](https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)
- Oracle's own marketing and FAQ pages refused my automated requests, so the card requirement comes from third-party guides, such as [this one](https://www.issoh.co.jp/tech/details/17531/). Check it at sign-up.

**Northflank**
- [Security incident status page](https://status.northflank.com/cmvv0rn5a092f14rutwccodpp)
- [Pricing](https://northflank.com/pricing)
- [Billing docs: "should not be used for production" and the payment method rule](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)
- [BleepingComputer's report on the incident](https://www.bleepingcomputer.com/news/security/northflank-security-breach-leads-to-abuse-of-customers-ai-api-keys/)

**Others**
- Railway: [plans](https://docs.railway.com/reference/pricing/plans), [pricing](https://railway.com/pricing), [free trial](https://docs.railway.com/reference/pricing/free-trial)
- Render: [free instances](https://render.com/docs/free), [background workers](https://render.com/docs/background-workers), [compute plans](https://render.com/docs/compute-plans). The $7 figure comes from [a third-party comparison](https://devtoolpicks.com/blog/railway-vs-render-vs-fly-io-solo-developers-2026).
- Koyeb: [instances](https://www.koyeb.com/docs/reference/instances), [scale to zero](https://www.koyeb.com/docs/run-and-scale/scale-to-zero)
- Hetzner: [cost-optimized plans](https://www.hetzner.com/cloud/cost-optimized/)
- AWS: [Lightsail pricing](https://aws.amazon.com/lightsail/pricing/), [free plan](https://aws.amazon.com/free/)

**Tools**
- [Installing uv](https://docs.astral.sh/uv/getting-started/installation/)
- [How `uv run` handles signals](https://docs.astral.sh/uv/concepts/projects/run/)
- [GitHub deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [systemd service settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [systemd kill settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)
