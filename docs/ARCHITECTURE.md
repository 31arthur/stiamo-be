# Architecture

## Services

Each service is an independent deployable with its **own database** and no imports from another service. They share only `stiamo_common` (error shape, token verification).

```
client ──► identity  (register / login / refresh / logout)  ── users DB
   │
   └─────► records   (CRUD on user-owned documents)         ── records DB
              ▲ verifies the access token locally (no call to identity)
```

New capabilities (files, notifications, realtime) become new services under `services/`.

## Layers inside a service (dependencies point inward)

```
presentation  FastAPI routers, request/response schemas, dependency wiring
application   use cases (one class per intent), ports (abstract repositories, hashers, token issuers)
domain        entities and rules, no framework imports
infrastructure SQLAlchemy models/repositories, bcrypt, PyJWT adapters
```

`domain` and `application` never import FastAPI, SQLAlchemy or PyJWT; infrastructure implements the ports and `presentation/deps.py` is the composition root.

## Authentication

- **Access token**: short-lived JWT (default 15 min), `typ=access`, sent as `Authorization: Bearer`. Verified locally by every service using `iss`, `aud`, `exp`, `typ`.
- **Refresh token**: JWT (default 30 days), `typ=refresh`, stored server-side as a SHA-256 hash keyed by `jti`. **Rotated on every use**; presenting an already-used token revokes the whole token family (theft detection).
- Algorithm is configurable: `HS256` with a shared secret for development, or `RS256` where identity holds the private key and other services only the public key.
- Passwords are hashed with bcrypt.

## Data

The frontend stores each aggregate as a JSON document (`id`, `userId`, ...). `records` keeps that contract: one table keyed by `(user_id, collection, id)` with a JSON `data` column; ownership is taken from the token, never from the payload.

## The working domain

`practice` holds the domain logic that used to live in the frontend (`src/domain`): outcomes, follow-up chasing,
appointment confirmations, case numbers and counsel engagements, hearings and the case diary, plans and their steps,
goals, links, history, the relation thread and the agenda. Each aggregate has a typed relational table (keys are
`(user_id, id)`; ownership is enforced in every query), with small nested lists in JSON columns and join tables for
the many-to-many relations (plan coverage, goal plans, hearings shared by several cases).

`files` keeps attachment metadata and the bytes (a mounted volume, behind a `FileStorage` port so object storage can
replace it). The two services talk over HTTP with the caller's own token: `files` tells `practice` to write
"attached/removed" lines to a record's history, and `practice` tells `files` to clear or move attachments when a record
is deleted or merged. Both calls are best-effort and never fail the user's change.

`drive` searches the owner's Google Drive (names and contents) with a read-only service account and copies nothing; it
also streams a file's bytes so the app can show it without a Google sign-in (see [DRIVE_SETUP.md](DRIVE_SETUP.md)).

## AI agents

`agent` is the one service that talks to AI providers (OpenAI, Gemini, OpenRouter, and the Jev decision model) and to the
chat channels (Telegram, WhatsApp). It reads the practice's data from `practice`, `files` and `drive` with the caller's own
token, so it has no special access. Several agents live in it, each a small definition built from reusable **tool packs**;
what a person chooses (keys, models, prompts, channels) is stored per person, sealed where it is secret. How it is
structured, the rules, and how to add an agent: [AI_AGENTS.md](AI_AGENTS.md) and [ADDING_AN_AGENT.md](ADDING_AN_AGENT.md);
running it: [AGENT_SETUP.md](AGENT_SETUP.md).

See [API.md](API.md) for every endpoint.
