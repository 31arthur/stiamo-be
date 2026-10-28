"""Imports the accounts from a Supabase export, keeping each user's id and password.

Supabase stores passwords as bcrypt hashes, which this service verifies as they are, so people sign in with the
password they already use. Keeping the user ids means their imported records stay theirs.

    docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" --entrypoint python identity \
        -m app.tools.import_users /tmp/export.json [--dry-run]

Safe to run again: existing ids and emails are left alone.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.db import make_engine, make_session_factory

from app.config import get_settings
from app.domain.entities import User
from app.infrastructure.db.models import UserModel
from app.infrastructure.db.repositories import SqlUserRepository

#: Not a valid bcrypt hash, so nobody can ever sign in to an account that had no password.
NO_PASSWORD = "!no-password"


@dataclass
class Report:
    imported: int = 0
    already_there: int = 0
    skipped: list[str] = field(default_factory=list)
    without_password: list[str] = field(default_factory=list)


def _created(value: str | None) -> datetime:
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


async def import_users(session: AsyncSession, users: list[dict], dry_run: bool = False) -> Report:
    report, repo = Report(), SqlUserRepository(session)
    known_ids = set((await session.execute(select(UserModel.id))).scalars())
    known_emails = set((await session.execute(select(UserModel.email))).scalars())
    for u in users:
        email = (u.get("email") or "").strip().lower()
        if not u.get("id") or not email:
            report.skipped.append(f"{u.get('id') or '?'}: no id or email")
            continue
        if u["id"] in known_ids or email in known_emails:
            report.already_there += 1
            continue
        hashed = u.get("passwordHash") or ""
        if not hashed.startswith("$2"):
            hashed = NO_PASSWORD
            report.without_password.append(email)
        if not dry_run:
            await repo.add(User(id=u["id"], email=email, name=(u.get("name") or email.split("@")[0]).strip(),
                                password_hash=hashed, created_at=_created(u.get("createdAt"))))
        known_ids.add(u["id"])
        known_emails.add(email)
        report.imported += 1
    return report


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export")
    ap.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    args = ap.parse_args()

    with open(args.export, encoding="utf-8") as f:
        users = json.load(f)["users"]
    engine = make_engine(get_settings().database_url)
    try:
        async with make_session_factory(engine)() as session:
            report = await import_users(session, users, args.dry_run)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"{'Would import' if args.dry_run else 'Imported'} {report.imported} users; {report.already_there} already there.")
    for line in report.skipped:
        print("  skipped:", line)
    if report.without_password:
        print("  no password hash (signed up another way); they cannot sign in until a reset is available:")
        for email in report.without_password:
            print("   -", email)
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
