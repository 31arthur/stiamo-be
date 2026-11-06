# Google Contacts in Stiamo

Bring your Google contacts into the workspaces they belong to: your chambers' address book into S&B, your own into Personal, and so on.

- **A workspace can have several Google accounts.** Link your chambers account and your Gmail to S&B, and S&B shows both address books together, each contact marked with the account it came from.
- **One Google account can be in several workspaces.** Link `me@gmail.com` to Personal, then add the same account to FSA with one click (no second sign-in). Each workspace has its own copy, so it is complete on its own.
- **In *All workspaces*** you see everyone together. A contact that is in two workspaces through the same Google account is shown once, with both workspaces named.

```
Google account ──OAuth (read contacts only)──► calendar service ──► the Contacts page (one workspace, or all together)
   (one or many per workspace;                  (the same Google sign-in as the calendar,
    one account in one or many workspaces)       its own permission)
```

## What it can and can't do

- **Reads** your Google contacts: name, organisation and title, email addresses, phone numbers, notes and photo. It is kept current every hour and whenever you press **Sync now**.
- **Read only.** Stiamo asks Google for `contacts.readonly`: it cannot add, change or delete a contact in Google. Edit a contact in Google (<https://contacts.google.com>) and it follows within the hour.
- It asks for nothing else for contacts, and the calendar's permission is separate (linking contacts doesn't touch your calendar, and the other way round). You can withdraw it at any time at <https://myaccount.google.com/permissions>.
- Refresh tokens are kept sealed with the same `CALENDAR_SECRET_KEY` as the calendar and never returned by the API. Unlinking an account forgets its contacts here; Google's permission is revoked when the *last* workspace using that account is unlinked.
- It reads "My contacts" (the ones you saved). People Google only suggests ("Other contacts", from mail you've exchanged) are not included.

## 1. Google Cloud (5 minutes; the same project and OAuth client as the calendar)

There is nothing new to create. If you haven't set up the calendar yet, do steps 1 to 4 of [CALENDAR_SETUP.md](CALENDAR_SETUP.md) first, then come back here.

1. <https://console.cloud.google.com> → pick the same project (`Stiamo`).
2. **APIs & Services → Library** → search **People API** → **Enable**. (It is a different API from Google Calendar's; without it Stiamo says *"People API has not been used in project … before or it is disabled"*.)
3. **APIs & Services → OAuth consent screen → Data access** (or **Scopes → Add or remove scopes**): add
   `https://www.googleapis.com/auth/contacts.readonly`. Save.
4. **Test users**: every Google account you will link for contacts must be listed (the same list as for the calendar). If the app is *Published*, nothing to do.
5. **Credentials**: nothing to change. Contacts use the **same redirect URI** as the calendar,
   `https://stiamo-be.fullstackarthur.com/v1/calendar/oauth/callback`, so you don't add a new one.

## 2. On the server

No new settings and no new database: contacts live inside the calendar service and use its OAuth client and its keys. Just deploy the new version:

```bash
cd ~/stiamo-be
./deploy.sh
```

The calendar service runs its new migration (`0002`) when it starts. Check that it is up (expect **401**: the route exists and wants a sign-in):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://stiamo-be.fullstackarthur.com/v1/calendar/contacts/status
```

Optional: `CALENDAR_CONTACTS_SYNC_MINUTES` (default `60`) changes how often contacts are read again; `0` turns the background read off (**Sync now** still works).

## 3. Link your accounts in Stiamo

Open **Contacts** in the sidebar of the workspace you want, at <https://stiamo.fullstackarthur.com>.

1. Press **Google accounts** (top right), then **Link a Google account**. The workspace is already chosen; change it if you like.
2. Sign in to Google, tick that Stiamo may *see your contacts*, and you land back on the Contacts page with the contacts loaded.
3. Another account for the same workspace: **Link a Google account** again and sign in with the other one.
4. The same account in another workspace: under that account press **Add to another workspace** and pick it. No sign-in is needed.
5. **Sync now** reads again straight away; **Unlink** removes an account from that workspace (its contacts go with it).

Use the search box to find someone by name, organisation, address, number or a word in their notes.

## If something goes wrong

| You see | Why | Fix |
| --- | --- | --- |
| *Google isn't set up on the server yet* | The OAuth client isn't on the server | Do [CALENDAR_SETUP.md](CALENDAR_SETUP.md) |
| *People API has not been used in project…* | The People API isn't enabled | Step 1.2 above, wait a minute, press **Sync now** |
| Google says *Access blocked* or *This app isn't verified* | The account isn't a test user, or the app is unverified | Add it under Test users, or **Advanced → Go to Stiamo** |
| *Didn't give lasting access* | Stiamo was allowed before, so Google sent no new token | Remove Stiamo at <https://myaccount.google.com/permissions>, then link again |
| An account shows **Link again** | Google withdrew the permission (or the token expired while the app was in *Testing*, 7 days) | Press **Link again**. Publishing the app stops the 7-day expiry |
| An account shows 0 contacts | It has no saved contacts, only suggested ones | Save them in Google Contacts |
