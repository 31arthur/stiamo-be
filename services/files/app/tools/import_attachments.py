"""Imports the attachments from a Supabase export: the records, and the files downloaded by tools/export_supabase.py.

    docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" -v "$PWD/supabase-files:/tmp/supabase-files:ro" \
        --entrypoint python files -m app.tools.import_attachments /tmp/export.json /tmp/supabase-files [--dry-run]

Linked documents (Google Docs and the like) need no file. Safe to run again: the same attachment is saved by id.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.db import make_engine, make_session_factory
from stiamo_common.models import to_iso

from app.application.ports import AttachmentRepository, FileStorage
from app.config import get_settings
from app.domain.attachment import LINK_MIME, Attachment, DocumentLink, EntityRef
from app.infrastructure.db.repository import SqlAttachmentRepository
from app.infrastructure.storage import LocalFileStorage


@dataclass
class Report:
    files: int = 0
    links: int = 0
    skipped: list[str] = field(default_factory=list)


async def import_attachments(repo: AttachmentRepository, storage: FileStorage, rows: list[dict], files_dir: Path,
                             dry_run: bool = False) -> Report:
    report, now = Report(), to_iso(datetime.now(UTC))
    for row in rows:
        doc, user_id = row.get("data") or {}, row.get("userId")
        label = f"{doc.get('name') or '?'} ({doc.get('id') or '?'})"
        backend = doc.get("backend")
        if not user_id or not doc.get("id"):
            report.skipped.append(f"{label}: no id or owner")
            continue
        try:
            common = dict(
                id=doc["id"], user_id=user_id, owner=EntityRef.model_validate(doc.get("owner") or {}),
                name=doc.get("name") or "file", caption=doc.get("caption"),
                created_at=doc.get("createdAt") or now, updated_at=doc.get("updatedAt") or doc.get("createdAt") or now)
            if backend == "link" and doc.get("link"):
                attachment = Attachment(**common, mime=LINK_MIME, size=0, backend="link",
                                        link=DocumentLink.model_validate(doc["link"]))
            elif backend == "storage" and doc.get("path"):
                source = files_dir / doc["path"]
                if not source.is_file():
                    report.skipped.append(f"{label}: file not found at {doc['path']}")
                    continue
                data = source.read_bytes()
                path = f"{user_id}/{doc['id']}"
                attachment = Attachment(**common, mime=doc.get("mime") or "application/octet-stream", size=len(data),
                                        backend="storage", path=path)
                if not dry_run:
                    await storage.put(path, data)
            else:
                report.skipped.append(f"{label}: stored only in a browser ({backend}); nothing to import")
                continue
        except ValidationError as e:
            report.skipped.append(f"{label}: {e.errors()[0]['loc']}: {e.errors()[0]['msg']}")
            continue
        if not dry_run:
            await repo.save(attachment)
        if attachment.backend == "link":
            report.links += 1
        else:
            report.files += 1
    return report


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export")
    ap.add_argument("files_dir", help="the folder tools/export_supabase.py downloaded the files into")
    ap.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    args = ap.parse_args()

    with open(args.export, encoding="utf-8") as f:
        rows = json.load(f)["tables"].get("attachments", [])
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            session: AsyncSession
            report = await import_attachments(SqlAttachmentRepository(session), LocalFileStorage(settings.storage_dir),
                                              rows, Path(args.files_dir), args.dry_run)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"{'Would import' if args.dry_run else 'Imported'} {report.files} files and {report.links} links.")
    for line in report.skipped:
        print("  skipped:", line)
    return 1 if report.skipped else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
