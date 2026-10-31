# agent service

AI providers (OpenAI, Gemini, OpenRouter), the agents and their prompts, the Jev decision model, and the Telegram and
WhatsApp channels. Agents are small definitions built from reusable tool packs; people's keys, models, prompts and
channels are stored per person.

- How it is built and the rules: [../../docs/AI_AGENTS.md](../../docs/AI_AGENTS.md)
- Adding an agent, tool pack, provider or channel: [../../docs/ADDING_AN_AGENT.md](../../docs/ADDING_AN_AGENT.md)
- Keys, Telegram, WhatsApp, deployment: [../../docs/AGENT_SETUP.md](../../docs/AGENT_SETUP.md)
- Endpoints: [../../docs/API.md](../../docs/API.md)

```bash
pip install -e ../../libs/stiamo_common -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8006
pytest
```

Tests use in-memory SQLite, scripted fake models and fake Telegram/Twilio: no network and no keys.
