# Urgent mail alerts

An agent that watches a mailbox can ping your phone the moment something arrives that can't wait: a loud Telegram message, a link that opens the email in the app, and buttons to answer it.

## Setting it up

1. **The agent.** In Developer, make an agent that reads the S&B mailbox (the *Urgent mail watch* template does this). Give one label the route **Ping me on Telegram** (`alert`) and describe in its prompt what is urgent: something to share, register for, confirm or pay by a deadline. Switch on **Listen to the mailbox** so it runs the moment mail arrives.
2. **Telegram.** Connect your bot and pair your chat (Assistant → On your phone). Alerts go to every paired chat.
3. **The link.** Set `AGENT_APP_URL` on the agent service to your site's address (for example `https://your-stiamo-site.netlify.app`). Without it the alert has no "Open the email" button.
4. **Themes** (Developer → Design). One for each organisation you write to: how a reply sounds (tone, language), how it opens and closes (greeting, sign-off, signature) and how it looks (letterhead, accent colour, footer). A theme is chosen by the sender's address, then their domain, then words in the mail, then your default.
5. **Try it.** Developer → Design → *Send a sample alert* sends a made-up urgent email to your phone so you can see the look and try the buttons. Nothing is sent to anyone from a sample.

## A Telegram bot for each workspace

Each workspace can have its own **email bot**, so S&B's mail doesn't land in the same chat as personal mail. Make a bot in @BotFather for the workspace, then in the app: Assistant → **On your phone** → **Email bots**, paste its token and press Start in the bot. One bot per workspace.

- An alert about a mailbox goes to the bot of **that mailbox's workspace**, never another's. A workspace with no email bot of its own falls back to your general bot (the one under Telegram).
- Email bots carry email alerts only: the evening plan, call reminders and voice messages go to the general bot.

## Each agent chooses

On a mail agent's setup (Developer → the agent → Settings) there is an **Email updates** option:

- **Send updates to Telegram**: what the agent queues for you (Attention, events) also arrives on the workspace's bot. Mail the model judges high priority, and any label set to *Ping me on Telegram*, comes as the loud 🚨 alert; the rest as a calmer 📬 *Needs you* update. Both carry the link and suggested replies.
- **Call me for priority mail**: for a loud alert, also ring you on Telegram (CallMeBot, at the username or number set under Call reminders): "Stiamo. Urgent email in S&B, from …: …. Check Telegram." A burst of urgent mail is one call: the same workspace isn't called again within 15 minutes.

No voice messages are sent for email: the Telegram notification is enough, and the call is kept for what is urgent.

## What happens on an alert

The message is built to look nothing like Stiamo's quiet ones (plans, reminders, answers): `🚨🚨 URGENT · S&B 🚨🚨`, the organisation's theme, the subject, who it is from, what it asks, what to do and by when. Under it:

- **Open the email in Stiamo** opens it in the app (`/sb/mail?a=…&m=…`).
- **1, 2, 3**: suggested replies (the agent reads the whole email and suggests up to three). Pressing one has a model write it in the organisation's theme and shows you the draft.
- **Send / Redo / Cancel** under the draft. Nothing is sent until you press **Send**; it goes out from the mailbox itself, as a reply in the same thread, dressed in the theme's letterhead.
- **Write my own**: type what you want to say, as roughly as you like, and it is written up in the theme.
- **Handled** closes the alert.

Each email raises one alert, however many times the agent runs. The email also appears in Attention, as before.

## Sending needs SMTP

Replies go out over SMTP from the same mailbox you read over IMAP, with the same username and password. The SMTP server is worked out from the IMAP one (`imap.example.com` → `smtp.example.com`, port 465 or 587; Microsoft 365 and Outlook are recognised). If a provider uses something else, the error names the server it tried. A sent reply is not copied into the mailbox's Sent folder.

## Deploying

The agent service has a migration (`0014`: the themes, and the alerts table); the mail service has a new `/send` endpoint. `./deploy.sh` runs both. On start the agent service re-registers each Telegram bot's webhook once so Telegram sends button presses to it (this needs `AGENT_PUBLIC_URL`).
