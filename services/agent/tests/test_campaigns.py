"""Campaigns: one message and link for many contacts, sent one by one by the owner, and what came of it."""

from app.application.campaigns import render_message
from app.infrastructure.linkpreview import is_public_url, parse_preview

from .conftest import headers

C = "/v1/agent/campaigns"
URL = "https://example.org/report"
PEOPLE = [
    {"id": "p1", "name": "Ali Hassan", "emails": [{"value": "ali@gwu.org"}], "phones": [{"value": "+974 5555 1212"}]},
    {"id": "p2", "name": "Ramesh Kumar", "emails": [], "phones": [{"value": "98765 43210"}]},
    {"id": "p3", "name": "No Number", "emails": [{"value": "nn@x.org"}], "phones": []},
]


def test_a_pages_own_sharing_tags_are_what_whatsapp_shows():
    html = '<html><head><title>Fallback</title><meta property="og:title" content="The 2026 wage report"><meta property="og:description" content="What  workers earn"><meta property="og:image" content="/cover.jpg"><meta property="og:site_name" content="Example"></head></html>'
    p = parse_preview(html, "https://example.org/a/b")
    assert p["title"] == "The 2026 wage report" and p["description"] == "What workers earn" and p["image"] == "https://example.org/cover.jpg" and p["site"] == "Example"
    assert parse_preview("<title>Just a title</title>", "https://www.site.org/x")["title"] == "Just a title"
    assert not is_public_url("http://localhost/x") and not is_public_url("http://127.0.0.1/") and not is_public_url("http://10.0.0.5/") and not is_public_url("file:///etc/passwd") and not is_public_url("ftp://x.org/")


def test_each_person_gets_their_first_name_and_the_link_goes_where_it_is_asked_or_at_the_end():
    assert render_message("Hi {name}, our new report: {link} Please share.", URL, "Ali Hassan") == f"Hi Ali, our new report: {URL} Please share."
    assert render_message("Hi {name}, our new report.", URL, "Ali Hassan") == f"Hi Ali, our new report.\n{URL}"
    assert render_message("", URL, "Ali") == URL


async def test_a_campaign_is_made_with_a_preview_people_are_added_and_each_gets_their_own_whatsapp_link_and_the_results_are_tracked(rig):
    rig.app.state.settings.public_url = ""
    pre = (await rig.client.post(f"{C}/preview", json={"url": URL}, headers=headers())).json()
    assert pre["title"] == "The 2026 wage report" and pre["image"].endswith("cover.jpg")
    assert (await rig.client.post(f"{C}/preview", json={"url": "https://nowhere.example/x"}, headers=headers())).json()["error"]
    made = await rig.client.post(C, json={"name": "Wage report", "message": "Hi {name}, our wage report is out.", "url": URL, "track_clicks": True}, headers=headers())
    assert made.status_code == 200, made.text
    camp = made.json()
    assert camp["preview"]["title"] == "The 2026 wage report" and camp["tracking"] is False  # no public https address in tests: the page's own address is used
    added = (await rig.client.post(f"{C}/{camp['id']}/contacts", json={"contacts": PEOPLE}, headers=headers())).json()
    assert added["added"] == 3 and added["counts"]["total"] == 3 and added["counts"]["pending"] == 3
    again = (await rig.client.post(f"{C}/{camp['id']}/contacts", json={"contacts": PEOPLE[:1]}, headers=headers())).json()
    assert again["added"] == 0 and again["alreadyIn"] == 1  # a person is in a campaign once
    members = {m["name"]: m for m in again["members"]}
    ali = members["Ali Hassan"]
    assert ali["text"] == f"Hi Ali, our wage report is out.\n{URL}" and ali["whatsapp"].startswith("https://wa.me/97455551212?text=Hi%20Ali%2C%20our%20wage%20report")
    assert members["No Number"]["whatsapp"] == "" and members["No Number"]["phone"] is False
    done = (await rig.client.post(f"{C}/members/{ali['id']}", json={"state": "sent"}, headers=headers())).json()
    assert done["counts"]["sent"] == 1 and done["counts"]["pending"] == 2 and next(m for m in done["members"] if m["name"] == "Ali Hassan")["sentAt"]
    replied = (await rig.client.post(f"{C}/members/{ali['id']}", json={"state": "replied", "note": "Wants the Hindi version"}, headers=headers())).json()
    assert replied["counts"]["replied"] == 1 and next(m for m in replied["members"] if m["name"] == "Ali Hassan")["note"] == "Wants the Hindi version"
    listed = (await rig.client.get(C, headers=headers())).json()["items"]
    assert listed[0]["name"] == "Wage report" and listed[0]["counts"]["replied"] == 1
    gone = (await rig.client.delete(f"{C}/members/{members['No Number']['id']}", headers=headers())).json()
    assert gone["counts"]["total"] == 2
    assert (await rig.client.post(f"{C}/members/{ali['id']}", json={"state": "exploded"}, headers=headers())).status_code == 422
    assert (await rig.client.post(C, json={"name": "Empty"}, headers=headers())).status_code == 422
    assert (await rig.client.post(C, json={"name": "Bad", "url": "ftp://x"}, headers=headers())).status_code == 422
    assert (await rig.client.delete(f"{C}/{camp['id']}", headers=headers())).status_code == 204
    assert (await rig.client.get(f"{C}/{camp['id']}", headers=headers())).status_code == 404


async def test_a_persons_own_link_shows_whatsapp_the_preview_and_counts_when_a_person_opens_it(rig):
    rig.app.state.settings.public_url = "https://stiamo.example"
    camp = (await rig.client.post(C, json={"name": "Wage report", "message": "Hi {name}: {link}", "url": URL}, headers=headers())).json()
    assert camp["tracking"] is True
    added = (await rig.client.post(f"{C}/{camp['id']}/contacts", json={"contacts": PEOPLE[:2]}, headers=headers())).json()
    ali = next(m for m in added["members"] if m["name"] == "Ali Hassan")
    link = ali["text"].split(": ")[1]
    assert link.startswith("https://stiamo.example/c/")
    path = link.removeprefix("https://stiamo.example")
    seen = await rig.client.get(path, headers={"user-agent": "WhatsApp/2.23.20.0 A"})  # WhatsApp making the preview
    assert seen.status_code == 200 and 'property="og:title" content="The 2026 wage report"' in seen.text and 'og:image" content="https://example.org/cover.jpg' in seen.text
    assert next(m for m in (await rig.client.get(f"{C}/{camp['id']}", headers=headers())).json()["members"] if m["name"] == "Ali Hassan")["clicks"] == 0  # a crawler is not a person
    for _ in range(2):
        go = await rig.client.get(path, headers={"user-agent": "Mozilla/5.0 (iPhone)"}, follow_redirects=False)
        assert go.status_code == 302 and go.headers["location"] == URL
    after = (await rig.client.get(f"{C}/{camp['id']}", headers=headers())).json()
    assert next(m for m in after["members"] if m["name"] == "Ali Hassan")["clicks"] == 2 and after["counts"]["clicked"] == 1
    assert (await rig.client.get("/c/not-a-real-token")).status_code == 404
