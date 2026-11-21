# Rapport plans: keeping business contacts warm

Every **new** Google contact, in any workspace, gets a get-to-know plan on its own (contacts already there when an account was first read are not "new"). The owner can leave
anyone out, and can attach a plan to a contact they already know.

## What the code decides (domain/rapport.py)
- **Tiers.** A (key): touches on days 0, 2, 5, 10, 18, 30, 45, 60, 90, then every 75 days. B (usual): 0, 3, 10, 25, 50, 90, then every 105. C (light): 0, 7, 30, 90, then every 182.
- **A reason every time.** intro, value, ask, connect, invite; then sector news, milestone, resource, event, annual review, festival. Never a bare "how are you".
- **When.** Inside the owner's window (10:00-15:00 IST) and the contact's working hours (09:00-18:00 their time); if they never meet (Brazil), the contact's hours win. No Sundays; Friday and Saturday for the Gulf.
- **Limits.** At most 3 messages a day across everyone, at most 4 to one person in 30 days, at least 2 days apart.
- **Backoff.** Two messages with no answer double the gaps; three pause the plan for 60 days. A reply (or the owner saying so) warms it again.
- **The contact's zone** comes from the contact (set on the web), else their phone's country code.

## What the model does
Only writes each message, at the moment it goes, from the contact's tags and profile, the purpose, and what was already sent (60 words, their language, one purpose, one easy next step).
Without a model it writes a plain message from the purpose.

## Sending
Auto-send on WhatsApp (Twilio). If WhatsApp will not take it (a business-initiated message needs an approved template or an open 24-hour chat), or there is no number or channel:
an action item and a Telegram message with the text written and a button that opens WhatsApp with it filled in (Sent, They replied, Reword, Skip, Pause).
A reply on the WhatsApp number from someone with a plan marks it replied and tells the owner.

## Telegram and the web
New contact: "New contact: Ali Hassan (S&B). I've started a plan, tier B" with Tier A / Tier C / No plan. "Who did I add this week?" walks the new contacts one at a time (Start plan, Tier A, No plan, Next).
Tags, profile, zone and "no plan" are edited on the web contacts page (kept in Stiamo; nothing is written to Google).
