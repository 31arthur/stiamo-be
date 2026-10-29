# Searching your Google Drive from Stiamo

Stiamo searches the **names and contents** of the letters in your Drive. Nothing is copied: Google does the searching,
and Stiamo only receives each match's name, date and link (which you can attach to a case).

```
Laptop "Letters" ──Drive for desktop──► Google Drive ◄── read-only service account ◄── drive service (VPS) ◄── Stiamo
```

Access is limited three ways: the service account can only **view** what you share with it (just the `Letters`
folder); it signs in with a key that stays on your server; and the API only answers the email(s) you list.

## 1. Google Cloud (about 10 minutes, free)

1. Open <https://console.cloud.google.com> and sign in with the Google account that owns the Drive.
2. Project picker (top bar) → **New project** → name it `Stiamo` → **Create**, then select it.
3. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
4. **IAM & Admin → Service Accounts → Create service account**. Name: `stiamo-drive-reader`. Skip the optional
   "grant access" steps → **Done**.
5. Open the new service account → **Keys → Add key → Create new key → JSON → Create**. A `.json` file downloads.
   This is the key: treat it like a password and don't put it in Git or email it.
6. On the service account's page, copy its **email** (ends in `.iam.gserviceaccount.com`).

## 2. Share the folder with it

1. In Google Drive on the web, find the synced **Letters** folder → right-click → **Share**.
2. Paste the service account's email, set it to **Viewer**, untick "Notify people", **Share**.
3. Open the folder; the address looks like `https://drive.google.com/drive/folders/1AbC…`. The part after
   `folders/` is the **folder id**. Note it down.

Subfolders and new letters inherit the sharing automatically. Files only appear once Drive for desktop has finished
syncing them from your laptop.

## 3. Put it on the server

On your computer, copy the key to the VPS (replace the file name and host):

```bash
scp ~/Downloads/stiamo-xxxxxx.json root@<vps>:~/stiamo-be/secrets/drive.json
```

On the VPS, in `~/stiamo-be`:

```bash
mkdir -p secrets && chmod 700 secrets && chmod 600 secrets/drive.json
cat >> .env <<'EOF'
DRIVE_FOLDER_ID=<the folder id>
DRIVE_ALLOWED_EMAILS=<the email you sign in to Stiamo with>
EOF
# new compose file and deploy script, then redeploy
curl -fsSL https://raw.githubusercontent.com/31arthur/stiamo-be/main/deploy/docker-compose.prod.yml -o docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/31arthur/stiamo-be/main/deploy/deploy.sh && chmod +x deploy.sh
./deploy.sh
```

Check it (expect **401**, which means the route exists and wants a sign-in):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://stiamo-be.fullstackarthur.com/v1/drive/status
```

In Stiamo, the search box in the command palette (Ctrl/⌘+K) and the **Find in Drive** button on a case now work.

## What it can and can't do

- Finds files whose **name or contents contain every word you type**; use quotes for an exact phrase
  (`"WP 1290/2026"`). Works on Google Docs, PDFs, Word files, and (less reliably) scanned PDFs and images.
- Results are matched by whole words, with no highlighted snippet and no typo tolerance. That is Google's search.
- For a case, **Suggest letters** looks for the case number, the court's number, the client and the parties, and ranks
  files that mention more of them higher.
- If you delete a file or stop sharing the folder, its link in a case stops working.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| "Drive search isn't turned on for your account" | `DRIVE_ALLOWED_EMAILS` doesn't contain the email you sign in with. |
| "Google Drive isn't connected on the server yet" | `secrets/drive.json` is missing or unreadable; check the path and `docker compose logs drive`. |
| "Google Drive couldn't be reached" | Drive API not enabled (step 1.3), key revoked, or Google is having a moment. |
| Searches return nothing | The folder isn't shared with the service account, or Drive for desktop hasn't synced the files yet. |
| Everything returns 404 | The server's compose file is old: re-download it (above) and run `./deploy.sh`. |

To revoke access at any time: remove the service account from the folder's sharing, or delete its key in Google Cloud.
