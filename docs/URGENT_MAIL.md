# Urgent mail alerts

An agent that watches a mailbox can ping your phone the moment something arrives that can't wait: a loud Telegram message, a link that opens the email in the app, and buttons to answer it.

## Setting it up

1. **The agent: nothing to build.** Every workspace that has a mailbox connected gets a **Priority mail watch** agent on its own (Developer → All agents, or the *Priority mail watch* card at the top of it). It listens to the workspace's mailboxes all the time, is started again after every restart, and is pointed at new mailboxes within the hour (or at once when you open Developer). It sorts new mail into *urgent* (a Telegram alert), *important* (Attention) and *routine* (ignored), using a prompt written for that kind of work (S&B legal and advocacy, FSA tech consultancy, Media, Personal). It looks at the last day's mail when it starts, so it doesn't raise alarms about old mail. You can change anything in it like any agent, pause a workspace's watch from the card, or delete it (a deleted watch is not made again). The one thing it needs from you is a **model**: it follows the last model you chose, so if you have chosen one anywhere it already has one; otherwise choose one in its Settings.
2. **Telegram.** Connect your bot and pair your chat (Assistant → On your phone). Alerts go to every paired chat.
3. **The link.** `AGENT_APP_URL` is the address of the web app, `https://stiamo.fullstackarthur.com`; the production compose file already uses it as the default, so there is nothing to add unless the site moves. To change it, set `AGENT_APP_URL` in `~/stiamo-be/.env` on the VPS and run `./deploy.sh`. Without an address the alert has no "Open the email" button. An alert's link looks like `https://stiamo.fullstackarthur.com/sb/mail?a=<mailbox>&f=INBOX&m=<message>`.
4. **Themes** (Developer → Design). One for each organisation you write to: how a reply sounds (tone, language), how it opens and closes (greeting, sign-off, signature) and how it looks (letterhead, accent colour, footer). A theme is chosen by the sender's address, then their domain, then words in the mail, then your default.
5. **Try it.** Developer → Design → *Send a sample alert* sends a made-up urgent email to your phone so you can see the look and try the buttons. Nothing is sent to anyone from a sample.

## A Telegram bot for each workspace

Each workspace can have its own bot, which is also that workspace's assistant (see [WORKSPACE_ASSISTANTS.md](WORKSPACE_ASSISTANTS.md)), so S&B's mail doesn't land in the same chat as personal mail. Make a bot in @BotFather for the workspace, then in the app: Assistant → **On your phone** → **Workspace assistants**, paste its token and press Start in the bot. One bot per workspace.

- An alert about a mailbox goes to the bot of **that mailbox's workspace**, never another's. A workspace with no email bot of its own falls back to your general bot (the one under Telegram).
- A workspace bot carries that workspace's email alerts and its assistant's chat. The evening plan and call reminders go to the general bot.

## Each agent chooses

On a mail agent's setup (Developer → the agent → Settings) there is an **Email updates** option:

- **Send updates to Telegram**: what the agent queues for you (Attention, events) also arrives on the workspace's bot. Mail the model judges high priority, and any label set to *Ping me on Telegram*, comes as the loud 🚨 alert; the rest as a calmer 📬 *Needs you* update. Both carry the link and suggested replies.
- **Call me for priority mail**: for a loud alert, also ring you on Telegram (CallMeBot, at the username or number set under Call reminders): "Stiamo. Urgent email in S&B, from …: …. Check Telegram." A burst of urgent mail is one call: the same workspace isn't called again within 15 minutes.

No audio is sent for email: the Telegram notification is enough, and the call is kept for what is urgent.

## What happens on an alert

The message is built to look nothing like Stiamo's quiet ones (plans, reminders, answers): `🚨🚨 URGENT · S&B 🚨🚨`, the organisation's theme, the subject, who it is from, what it asks, what to do and by when. Under it:

- **Open the email in Stiamo** opens it in the app at `https://stiamo.fullstackarthur.com/sb/mail?a=…&m=…`. You need to be signed in there; Telegram opens it in its own browser or your default one.
- **1, 2, 3**: suggested replies (the agent reads the whole email and suggests up to three). Pressing one has a model write it in the organisation's theme and shows you the draft.
- **Send / Redo / Cancel** under the draft. Nothing is sent until you press **Send**; it goes out from the mailbox itself, as a reply in the same thread, dressed in the theme's letterhead.
- **Write my own**: type what you want to say, as roughly as you like, and it is written up in the theme.
- **Handled** closes the alert.

Each email raises one alert, however many times the agent runs. The email also appears in Attention, as before.

## Sending needs SMTP

Replies go out over SMTP from the same mailbox you read over IMAP, with the same username and password. The SMTP server is worked out from the IMAP one (`imap.example.com` → `smtp.example.com`, port 465 or 587; Microsoft 365 and Outlook are recognised). If a provider uses something else, the error names the server it tried. A sent reply is not copied into the mailbox's Sent folder.

## Deploying

The agent service has a migration (`0014`: the themes, and the alerts table); the mail service has a new `/send` endpoint. `./deploy.sh` runs both. On start the agent service re-registers each Telegram bot's webhook once so Telegram sends button presses to it (this needs `AGENT_PUBLIC_URL`).
