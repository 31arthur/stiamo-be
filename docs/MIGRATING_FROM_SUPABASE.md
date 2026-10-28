# Moving your data from Supabase to the VPS

What moves: **accounts** (same ids, same passwords), **every record** (tasks, meetings, follow-ups, cases, hearings,
plans, goals, counsel, clients, projects, links, history) and **attached files**. Nothing in Supabase is changed or
deleted: the export only reads.

Because user ids are kept, each person's records stay theirs, and people sign in with the password they already use
(Supabase and the new identity service both use bcrypt).

```
your computer ── export.json + supabase-files/ ──►  VPS  ──►  identity · practice · files
   ▲ reads
Supabase
```

## 0. Before you start

- The new stack is deployed and healthy on the VPS (`docs/DEPLOY.md`), including `practice` and `files`.
- Pick a quiet moment: changes made in the old app after the export are not carried over. Run the export once, import,
  check, then switch the frontend. (If you must re-run, the importers are repeatable: they update by id.)
- The export contains password hashes and all your data. Keep it off Git and cloud drives, and delete it afterwards.

## 1. Export (on your computer)

You need three things from the Supabase dashboard:

| Value | Where |
| --- | --- |
| `SUPABASE_DB_URL` | Project Settings → Database → Connection string (URI). Use the "Session pooler" one if your network has no IPv6. Put the database password in it. |
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Project Settings → API → `service_role` key (secret: it bypasses security rules; use it only here) |

```bash
pip install asyncpg httpx
export SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'
export SUPABASE_URL='https://<ref>.supabase.co'
export SUPABASE_SERVICE_KEY='...'
python tools/export_supabase.py --out export.json --files-dir supabase-files
```

It prints a row count per table and downloads each attached file. Use `--skip-files` to export the data only.
The counts should match what you see in Supabase (Table Editor).

## 2. Copy to the VPS

```bash
scp -r export.json supabase-files  root@<vps>:~/stiamo-be/
```

## 3. Import (on the VPS, in `~/stiamo-be`)

Run each with `--dry-run` first: it checks everything and writes nothing. Order matters only for readability: users,
records, then files.

```bash
# 1) accounts
docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" --entrypoint python identity \
  -m app.tools.import_users /tmp/export.json --dry-run

# 2) records
docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" --entrypoint python practice \
  -m app.tools.import_supabase /tmp/export.json --dry-run

# 3) attached files
docker compose run --rm -v "$PWD/export.json:/tmp/export.json:ro" -v "$PWD/supabase-files:/tmp/supabase-files:ro" \
  --entrypoint python files -m app.tools.import_attachments /tmp/export.json /tmp/supabase-files --dry-run
```

Read what each says. Then run the same three commands again **without** `--dry-run`.

### What the report tells you

- **Imported** counts per table.
- **skipped** lines name a record the new rules cannot keep, with the reason: a hearing whose case no longer exists, a
  date that is not a valid date, two cases with the same number, and so on. Nothing is silently changed. Fix the
  record in Supabase and run the export and import again, or leave it out.
- **note** lines say what was tidied: a plan that listed a case which no longer exists, counsel who was deleted while
  briefed on a case (the case is imported as handled by us), old case types mapped to the closest current one.
- Accounts listed as **no password hash** signed up another way (for example Google). They are imported but cannot sign
  in until password reset exists in the new backend.

The exit status is non-zero if anything was skipped, so scripts can notice.

## 4. Check

Sign in through the API with an old email and password, and compare a few things with the old app:

```bash
curl -s -X POST https://stiamo-be.fullstackarthur.com/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"<your old password>"}'
# then, with the access token:
curl -s https://stiamo-be.fullstackarthur.com/v1/cases -H "authorization: Bearer <token>"
curl -s https://stiamo-be.fullstackarthur.com/v1/attachments -H "authorization: Bearer <token>"
```

Case numbers continue from the highest imported one, appointments keep their confirmation calls, and plan progress and
goal health are computed from the imported outcomes.

## 5. Clean up

```bash
rm -rf ~/stiamo-be/export.json ~/stiamo-be/supabase-files     # on the VPS
rm -rf export.json supabase-files                              # on your computer
```

Keep the Supabase project until you have used the new backend for a while; pausing it costs nothing.

## Not carried over

- Browser-only data from the offline demo mode (never reached Supabase).
- Sessions: everyone signs in once more.
- Realtime history of deleted records (only what is in the tables exists).
