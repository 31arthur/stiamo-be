"""Deletes the old agents' traces: the run history (every prompt, input, tool call and answer) and the saved chat threads.

Run it once on the server, after the new version is up:

    docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.purge_traces          # shows what it would delete
    docker compose -f deploy/docker-compose.prod.yml exec agent python -m app.scripts.purge_traces --yes    # deletes it

What it does NOT touch, because it is in use: each agent's model, prompt and workspace settings, connected bots and channels, API keys, the cost
and usage ledger (the Usage page), proposals and plans waiting for approval, the delivery log, and the Task Force agents with their runs and
queues. `--include-usage` also clears the cost ledger. Deleting is permanent: there is no undo, so take a database backup first if in doubt.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, func, select
from stiamo_common.db import make_engine, make_session_factory

from app.config import get_settings
from app.infrastructure.db.models import ConversationModel, RunModel, UsageModel


async def main(apply: bool, include_usage: bool) -> None:
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    targets = [("agent runs (the history of every agent)", RunModel), ("saved chat threads", ConversationModel)]
    if include_usage:
        targets.append(("the cost and usage ledger", UsageModel))
    async with factory() as s:
        for label, model in targets:
            n = (await s.execute(select(func.count()).select_from(model))).scalar_one()
            print(f"{'Deleting' if apply else 'Would delete'} {n} rows: {label}")
            if apply:
                await s.execute(delete(model))
        if apply:
            await s.commit()
            print("Done.")
        else:
            print("Nothing was deleted. Run again with --yes to delete.")
    await engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--yes", action="store_true", help="actually delete (without it, only counts)")
    p.add_argument("--include-usage", action="store_true", help="also clear the cost and usage ledger")
    a = p.parse_args()
    asyncio.run(main(a.yes, a.include_usage))
