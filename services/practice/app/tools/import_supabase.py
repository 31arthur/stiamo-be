"""Imports the records from a Supabase export (see tools/export_supabase.py).

The documents have exactly the shape the frontend saved, so they are read with the same models the API uses. Older
documents are tidied on the way in (a plan's single caseId/hearingId become lists, an unknown case type becomes
"other"), and anything that cannot be kept (a hearing whose case is gone, a value that no longer validates) is
skipped and listed, never silently changed.

    docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" --entrypoint python practice \
        -m app.tools.import_supabase /tmp/export.json [--dry-run] [--report report.json]

Safe to run again: records are saved by id, so a second run updates rather than duplicates.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.db import make_engine, make_session_factory

from app.application.ports import Stores
from app.config import get_settings
from app.domain.cases import Hearing, LegalCase
from app.domain.common import to_dt, to_iso
from app.domain.directory import Client, Counsel, Project
from app.domain.goals import Goal
from app.domain.history import Activity, Link
from app.domain.plans import ActionPlan
from app.domain.work import FollowUp, Meeting, Task
from app.infrastructure.db.repositories import build_stores

#: Parents before children, so every reference can be checked against what was already imported.
ORDER: list[tuple[str, type[BaseModel], str]] = [
    ("counsel", Counsel, "counsel"), ("clients", Client, "client"), ("projects", Project, "project"),
    ("cases", LegalCase, "case"), ("hearings", Hearing, "hearing"), ("plans", ActionPlan, "plan"),
    ("goals", Goal, "goal"), ("tasks", Task, "task"), ("follow_ups", FollowUp, "follow_up"),
    ("meetings", Meeting, "meeting"), ("links", Link, "link"), ("activity", Activity, "activity"),
]
_CASE_TYPES = {"pow", "public_interest", "industrial_dispute", "minimum_wages", "workmen_compensation", "other"}


@dataclass
class Report:
    imported: dict[str, int] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def skip(self, table: str, id_: Any, why: str) -> None:
        self.skipped.append({"table": table, "id": str(id_), "reason": why})


def _unique(items):
    return list(dict.fromkeys(i for i in items if i))


def _prepare(table: str, doc: dict, user_id: str, now: str) -> dict:
    """Brings an older document up to the current shape."""
    doc = dict(doc)
    doc["userId"] = user_id
    doc.setdefault("createdAt", doc.get("updatedAt") or now)
    doc.setdefault("updatedAt", doc["createdAt"])
    if table == "cases":
        raw = doc.get("type")
        doc["type"] = raw if raw in _CASE_TYPES else "public_interest" if raw == "pil" else "other"
    if table == "plans":
        doc["caseIds"] = _unique([*(doc.get("caseIds") or []), doc.pop("caseId", None)])
        doc["hearingIds"] = _unique([*(doc.get("hearingIds") or []), doc.pop("hearingId", None)])
    return doc


async def import_export(session: AsyncSession, export: dict, dry_run: bool = False) -> Report:
    report, stores = Report(), build_stores(session)
    # Documents without timestamps get the export's own time, so running the import again changes nothing.
    exported = export.get("exportedAt")
    now = to_iso(to_dt(exported)) if exported else to_iso(datetime.now(UTC))
    known: dict[str, set[tuple[str, str]]] = {}  # table -> {(user id, record id)} that made it in
    seen_refs: set[tuple[str, str]] = set()

    for table, model, store_name in ORDER:
        rows = export.get("tables", {}).get(table, [])
        accepted: list[Any] = []
        for row in rows:
            user_id, doc = row.get("userId"), row.get("data") or {}
            rid = doc.get("id") or "?"
            if not user_id:
                report.skip(table, rid, "no owner")
                continue
            try:
                entity = model.model_validate(_prepare(table, doc, user_id, now))
            except ValidationError as e:
                first = e.errors()[0]
                report.skip(table, rid, f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
                continue
            entity = _check_references(table, entity, user_id, known, seen_refs, report)
            if entity is not None:
                accepted.append(entity)
                known.setdefault(table, set()).add((user_id, entity.id))
        report.imported[table] = len(accepted)
        if accepted and not dry_run:
            await _save(stores, store_name, table, accepted, known, report)
    return report


def _check_references(table, entity, user_id, known, seen_refs, report):
    """Drops what points at records that no longer exist, or skips the record when it cannot stand without them."""

    def alive(kind_table: str, id_: str) -> bool:
        return (user_id, id_) in known.get(kind_table, set())

    if table == "projects" and not alive("clients", entity.client_id):
        report.skip(table, entity.id, "its client is not in the export")
        return None
    if table == "cases":
        if (user_id, entity.reference) in seen_refs:
            report.skip(table, entity.id, f"case number {entity.reference} is used twice")
            return None
        seen_refs.add((user_id, entity.reference))
        if entity.engagement and not alive("counsel", entity.engagement.counsel_id):
            report.notes.append(f"case {entity.reference}: briefed counsel is gone; imported as handled by us")
            entity = entity.model_copy(update={"engagement": None})
    if table == "hearings":
        if not alive("cases", entity.case_id):
            report.skip(table, entity.id, "its case is not in the export")
            return None
        also = [c for c in entity.also_case_ids or [] if alive("cases", c)]
        entity = entity.model_copy(update={"also_case_ids": also or None})
    if table == "plans":
        cases = [c for c in entity.case_ids or [] if alive("cases", c)]
        hearings = [h for h in entity.hearing_ids or [] if alive("hearings", h)]
        if len(cases) != len(entity.case_ids or []) or len(hearings) != len(entity.hearing_ids or []):
            report.notes.append(f"plan {entity.title!r}: dropped cases or hearings that no longer exist")
        entity = entity.model_copy(update={"case_ids": cases or None, "hearing_ids": hearings or None})
    if table == "goals":
        plans = [p for p in entity.plan_ids if alive("plans", p)]
        if len(plans) != len(entity.plan_ids):
            report.notes.append(f"goal {entity.title!r}: dropped plans that no longer exist")
        entity = entity.model_copy(update={"plan_ids": plans})
    return entity


async def _save(stores: Stores, store_name: str, table: str, entities: list, known, report) -> None:
    repo = getattr(stores, store_name)
    try:
        async with repo._s.begin_nested():  # noqa: SLF001  (one savepoint so a bad batch can fall back to row by row)
            await repo.save_many(entities)
        return
    except IntegrityError:
        pass
    saved = 0
    for e in entities:
        try:
            async with repo._s.begin_nested():  # noqa: SLF001
                await repo.save(e)
            saved += 1
        except IntegrityError as exc:
            report.skip(table, e.id, f"database refused it: {str(exc.orig).splitlines()[0][:120]}")
            known.get(table, set()).discard((e.user_id, e.id))
    report.imported[table] = saved


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export")
    ap.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    ap.add_argument("--report", help="also write the report to this file")
    args = ap.parse_args()

    with open(args.export, encoding="utf-8") as f:
        export = json.load(f)
    engine = make_engine(get_settings().database_url)
    try:
        async with make_session_factory(engine)() as session:
            report = await import_export(session, export, args.dry_run)
            await session.commit()
    finally:
        await engine.dispose()

    print("DRY RUN: nothing was written." if args.dry_run else "Imported:")
    for table, n in report.imported.items():
        print(f"  {table:12} {n:6}")
    for note in report.notes:
        print("  note:", note)
    for s in report.skipped:
        print(f"  skipped {s['table']} {s['id']}: {s['reason']}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"imported": report.imported, "skipped": report.skipped, "notes": report.notes}, f, indent=1)
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
