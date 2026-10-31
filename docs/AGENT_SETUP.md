# The AI agent and its chat channels

The `agent` service keeps your AI provider keys (OpenAI, Google Gemini, OpenRouter), runs the case agent, and answers
messages from **Telegram** and **WhatsApp** the same way the web app does.

```
Stiamo web ──► /v1/agent/*  ──► agent ──► OpenAI / Gemini / OpenRouter (and Jev, a decision model, via OpenRouter)
Telegram ────► /v1/agent/webhooks/telegram/{id} ──┤
WhatsApp ────► /v1/agent/webhooks/whatsapp/{id} ──┘      └─► practice, files, drive (your cases and documents)
```

How the service is built, and how to add an agent, is in [AI_AGENTS.md](AI_AGENTS.md) and [ADDING_AN_AGENT.md](ADDING_AN_AGENT.md).

Everything below is set from the **AI agents** section of the app. This page is only what has to be done on the server, and
what to click on the Telegram and Twilio side.

## 1. On the server (once)

Add to `.env` next to `docker-compose.yml`:

```bash
AGENT_DB_PASSWORD=$(openssl rand -hex 24)
AGENT_SECRET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
AGENT_PUBLIC_URL=https://stiamo-be.fullstackarthur.com
```

- `AGENT_SECRET_KEY` encrypts the provider keys and channel tokens in the database. **Keep a copy**: if it is lost, the keys
  have to be entered again.
- `AGENT_PUBLIC_URL` is the https address Telegram and Twilio send messages to.

An existing stack needs its new database first, then a redeploy:

```bash
curl -fsSLO https://raw.githubusercontent.com/31arthur/stiamo-be/main/deploy/add-databases.sh && chmod +x add-databases.sh
curl -fsSL https://raw.githubusercontent.com/31arthur/stiamo-be/main/deploy/docker-compose.prod.yml -o docker-compose.yml
./add-databases.sh && ./deploy.sh
```

## 2. Providers and models

AI agents → **Providers**. Keys and providers are shared by every agent; each agent has its own model and prompt. Paste a key; it is checked with the provider before it is kept. The models and the thinking levels
each one accepts are asked of the provider, so the list is always current.

**Jev** (a decision model from TypeSafe) is reached through the OpenRouter key: it answers typed questions (pick one of
these, is this true, where on this scale) with probabilities instead of text. Turn it on under **Decisions**.

## 3. Telegram

1. In Telegram, open **@BotFather** → `/newbot` → choose a name and a username → copy the **token** it gives you.
2. AI agents → **Channels** → Telegram → paste the token → **Connect**. Stiamo registers the webhook itself.
3. Press **Open in Telegram** (or send the 8-character code to your bot as `/start CODE`). That chat is now linked.

Each channel is answered by one agent, chosen when you connect it and changeable in **Channels**. Only linked chats get answers; anyone else who finds the bot is told it isn't linked, and nothing reaches the model.

## 4. WhatsApp (through Twilio)

WhatsApp has no "bot token". The practical official route is Twilio:

1. Create a Twilio account → Console → **Messaging → Try it out → Send a WhatsApp message**. It shows the sandbox number
   (usually `+1 415 523 8886`) and a join phrase. From your phone, send `join <phrase>` to that number.
2. From the Twilio Console home copy the **Account SID** and **Auth Token**.
3. AI agents → **Channels** → WhatsApp → paste the SID and token, the sender number (the sandbox number), and **your own
   WhatsApp number** (the only one allowed to use the agent) → **Connect**.
4. Stiamo shows a **webhook address**. In the Twilio Console → Messaging → **Settings → WhatsApp sandbox settings**, paste it
   into *When a message comes in* (method POST) and save. (Twilio doesn't let an app set the sandbox's webhook for you.)
5. Press **Send a test message**, then message the number from your phone.

Notes: the sandbox needs you to re-send the `join` phrase every 72 hours, and replies must come within 24 hours of your last
message (a WhatsApp rule). A permanent sender of your own needs a WhatsApp Business profile approved through Twilio.

## Safety

- Provider keys and channel tokens are encrypted (Fernet) in the database and never sent back to the browser.
- The Telegram webhook checks the secret Telegram was given; the WhatsApp one checks Twilio's signature.
- Messages from numbers or chats that aren't yours are ignored.
- When a message arrives there is no signed-in browser, so the agent signs a five-minute token with the shared `JWT_SECRET`
  to read **your** cases, files and Drive, exactly as the web app would.
- The text of a question, and the cases and document names the tools find, are sent to the provider you chose.
