# Urgent mail alerts

An agent that watches a mailbox can ping your phone the moment something arrives that can't wait: a loud Telegram message, a link that opens the email in the app, and buttons to answer it.

## Setting it up

1. **The agent.** In Developer, make an agent that reads the S&B mailbox (the *Urgent mail watch* template does this). Give one label the route **Ping me on Telegram** (`alert`) and describe in its prompt what is urgent: something to share, register for, confirm or pay by a deadline. Switch on **Listen to the mailbox** so it runs the moment mail arrives.
2. **Telegram.** Connect your bot and pair your chat (Assistant → On your phone). Alerts go to every paired chat.
3. **The link.** Set `AGENT_APP_URL` on the agent service to your site's address (for example `https://your-stiamo-site.netlify.app`). Without it the alert has no "Open the email" button.
4. **Themes** (Developer → Design). One for each organisation you write to: how a reply sounds (tone, language), how it opens and closes (greeting, sign-off, signature) and how it looks (letterhead, accent colour, footer). A theme is chosen by the sender's address, then their domain, then words in the mail, then your default.
5. **Try it.** Developer → Design → *Send a sample alert* sends a made-up urgent email to your phone so you can see the look and try the buttons. Nothing is sent to anyone from a sample.

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
