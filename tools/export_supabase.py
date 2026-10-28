#!/usr/bin/env python
"""Exports everything from a Supabase project into one JSON file (and the attachment files into a folder).

Run it on your own computer. It only READS from Supabase.

    pip install asyncpg httpx
    export SUPABASE_DB_URL='postgresql://postgres:<db password>@db.<ref>.supabase.co:5432/postgres'
    export SUPABASE_URL='https://<ref>.supabase.co'           # only needed for the attached files
    export SUPABASE_SERVICE_KEY='<service_role key>'            # only needed for the attached files
    python tools/export_supabase.py --out export.json --files-dir supabase-files

The output holds password hashes and all your data: keep it private and delete it once the import is verified.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import asyncpg
import httpx

#: One table per collection; each row is (id, user_id, data jsonb).
TABLES = ["tasks", "meetings", "follow_ups", "cases", "hearings", "plans", "goals", "links", "activity",
          "attachments", "counsel", "clients", "projects"]
BUCKET = "attachments"


async def export_users(conn) -> list[dict]:
    rows = await conn.fetch(
        "select id::text, email, raw_user_meta_data, encrypted_password, created_at from auth.users order by created_at")
    users = []
    for r in rows:
        meta = r["raw_user_meta_data"] or {}
        users.append({
            "id": r["id"], "email": r["email"], "name": meta.get("name") or (r["email"] or "").split("@")[0],
            "passwordHash": r["encrypted_password"] or "", "createdAt": r["created_at"].astimezone(UTC).isoformat(),
        })
    return users


async def export_table(conn, table: str) -> list[dict]:
    rows = await conn.fetch(f'select user_id::text, data from public."{table}" order by user_id, id')  # noqa: S608
    return [{"userId": r["user_id"], "data": r["data"]} for r in rows]


def download_files(attachments: list[dict], base_url: str, key: str, out_dir: Path) -> tuple[int, list[str]]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    done, failed = 0, []
    with httpx.Client(timeout=60) as http:
        for row in attachments:
            doc = row["data"]
            path = doc.get("path")
            if doc.get("backend") != "storage" or not path:
                continue
            target = out_dir / path
            if target.exists():
                done += 1
                continue
            resp = http.get(f"{base_url.rstrip('/')}/storage/v1/object/authenticated/{BUCKET}/{quote(path)}", headers=headers)
            if resp.status_code != 200:
                failed.append(f"{path} (HTTP {resp.status_code})")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resp.content)
            done += 1
    return done, failed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="export.json")
    ap.add_argument("--files-dir", default="supabase-files")
    ap.add_argument("--skip-files", action="store_true", help="export the data only")
    args = ap.parse_args()

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("Set SUPABASE_DB_URL (Supabase dashboard → Project Settings → Database → Connection string).")
        return 2

    # statement_cache_size=0: the pooler (port 6543) does not support prepared statements
    conn = await asyncpg.connect(db_url, ssl="require", statement_cache_size=0)
    try:
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        export = {"exportedAt": datetime.now(UTC).isoformat(), "users": await export_users(conn), "tables": {}}
        for table in TABLES:
            export["tables"][table] = await export_table(conn, table)
            print(f"{table:12} {len(export['tables'][table]):6} rows")
    finally:
        await conn.close()

    Path(args.out).write_text(json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'users':12} {len(export['users']):6}\n-> {args.out}")

    if args.skip_files:
        return 0
    stored = [a for a in export["tables"]["attachments"] if a["data"].get("backend") == "storage"]
    if not stored:
        print("No stored files to download.")
        return 0
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (base and key):
        print(f"{len(stored)} attached files were not downloaded: set SUPABASE_URL and SUPABASE_SERVICE_KEY, or use --skip-files.")
        return 1
    done, failed = download_files(stored, base, key, Path(args.files_dir))
    print(f"files: {done} downloaded into {args.files_dir}/, {len(failed)} failed")
    for f in failed:
        print("  missing:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
