from urllib.parse import parse_qs, urlparse

from app.domain.contacts import ContactAccount, contact_from_person, merge_across

from .conftest import APP, headers, person

C = "/v1/calendar/contacts"
ADA = person("c1", "Ada Lovelace", "ada@labour.gov.in", "+91 98765 43210", "Labour Department")
BOB = person("c2", "Bob Nair", "bob@client.io")


async def start(rig, workspace="sb", who=None) -> str:
    r = await rig.client.post(f"{C}/oauth/start", json={"workspace": workspace}, headers=who or headers())
    assert r.status_code == 200, r.text
    assert "contacts.readonly" in r.json()["url"]  # it asks for contacts only, and read only
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


async def link(rig, email, people, workspace="sb", who=None):
    """The whole sign-in as the browser does it: start, Google, and back to the callback."""
    code = rig.people.connect(email, people)
    return await rig.client.get("/v1/calendar/oauth/callback", params={"code": code, "state": await start(rig, workspace, who)}, follow_redirects=False)


async def status(rig, who=None):
    return (await rig.client.get(f"{C}/status", headers=who or headers())).json()


async def listed(rig, who=None, **q):
    return (await rig.client.get(C, params=q, headers=who or headers())).json()


def test_a_google_person_becomes_a_contact_and_the_ones_with_nothing_to_show_are_skipped():
    a = ContactAccount("a1", "u1", "sb", "me@x.org", "r")
    c = contact_from_person(a, {**ADA, "biographies": [{"value": "Met at the tribunal"}], "photos": [{"url": "https://p/1", "default": True}, {"url": "https://p/2"}]})
    assert c and (c.name, c.organisation, c.title, c.notes, c.photo) == ("Ada Lovelace", "Labour Department", "Registrar", "Met at the tribunal", "https://p/2")
    assert c.emails == [{"value": "ada@labour.gov.in", "label": "work"}] and c.phones[0]["value"] == "+91 98765 43210" and "labour department" in c.search and c.id.startswith("a1:")
    assert contact_from_person(a, person("c3", email="only@x.org")).name == "only@x.org"  # no name: the address stands in
    assert contact_from_person(a, person("c4", org="Acme Ltd")).name == "Acme Ltd"
    assert contact_from_person(a, person("c5")) is None and contact_from_person(a, {**BOB, "metadata": {"deleted": True}}) is None and contact_from_person(a, {"names": []}) is None


async def test_linking_an_account_to_a_workspace_reads_its_contacts(rig):
    r = await link(rig, "me@sb.org", [ADA, BOB])
    assert r.status_code == 303 and r.headers["location"] == f"{APP}/sb/contacts?contacts_connected=me%40sb.org"
    s = await status(rig)
    assert s["configured"] is True and [(a["workspace"], a["email"], a["contacts"], a["status"]) for a in s["accounts"]] == [("sb", "me@sb.org", 2, "ok")]
    out = await listed(rig, workspace="sb")
    assert out["total"] == 2 and [c["name"] for c in out["items"]] == ["Ada Lovelace", "Bob Nair"] and out["items"][0]["accounts"] == ["me@sb.org"]
    assert (await listed(rig, workspace="fsa"))["total"] == 0  # nothing leaks into another workspace


async def test_a_workspace_can_have_several_accounts(rig):
    await link(rig, "chambers@sb.org", [ADA])
    await link(rig, "personal@gmail.com", [BOB], workspace="sb")
    assert [a["email"] for a in (await status(rig))["accounts"]] == ["chambers@sb.org", "personal@gmail.com"]
    out = await listed(rig, workspace="sb")
    assert [(c["name"], c["accounts"]) for c in out["items"]] == [("Ada Lovelace", ["chambers@sb.org"]), ("Bob Nair", ["personal@gmail.com"])]


async def test_one_account_can_be_in_several_workspaces_without_signing_in_again(rig):
    await link(rig, "me@x.org", [ADA, BOB], workspace="sb")
    account = (await status(rig))["accounts"][0]
    r = await rig.client.post(f"{C}/accounts/{account['id']}/link", json={"workspace": "fsa"}, headers=headers())
    assert r.status_code == 200 and r.json()["workspace"] == "fsa" and r.json()["contacts"] == 2
    assert (await listed(rig, workspace="sb"))["total"] == 2 and (await listed(rig, workspace="fsa"))["total"] == 2
    together = await listed(rig)  # every workspace at once: one person, shown once, with all their workspaces
    assert together["total"] == 2 and together["items"][0]["workspaces"] == ["fsa", "sb"]
    again = await rig.client.post(f"{C}/accounts/{account['id']}/link", json={"workspace": "fsa"}, headers=headers())
    assert again.status_code == 422 and "already linked" in again.json()["error"]["message"]
    assert (await rig.client.post(f"{C}/accounts/{account['id']}/link", json={"workspace": "nowhere"}, headers=headers())).status_code == 422


async def test_two_different_accounts_with_the_same_person_are_not_merged(rig):
    await link(rig, "a@x.org", [ADA], workspace="sb")
    await link(rig, "b@x.org", [{**ADA, "resourceName": "people/other"}], workspace="fsa")
    assert (await listed(rig))["total"] == 2  # different Google accounts: two contacts, however alike


async def test_search_looks_in_names_organisations_addresses_and_numbers(rig):
    await link(rig, "me@x.org", [ADA, BOB, person("c9", "Chitra Rao", phone="98400 11122")])
    names = lambda r: [c["name"] for c in r["items"]]  # noqa: E731
    assert names(await listed(rig, workspace="sb", q="labour")) == ["Ada Lovelace"]
    assert names(await listed(rig, workspace="sb", q="CLIENT.io")) == ["Bob Nair"]
    assert names(await listed(rig, workspace="sb", q="11122")) == ["Chitra Rao"]
    assert (await listed(rig, workspace="sb", q="zzz"))["total"] == 0
    page = await listed(rig, workspace="sb", limit=2, offset=1)
    assert page["total"] == 3 and names(page) == ["Bob Nair", "Chitra Rao"]


async def test_reading_again_follows_google_and_a_withdrawn_permission_says_to_link_again(rig):
    await link(rig, "me@x.org", [ADA, BOB])
    rig.people.people["me@x.org"] = [ADA, person("c7", "Dev Patel", "dev@x.org")]  # Bob gone, Dev new
    out = (await rig.client.post(f"{C}/sync", json={}, headers=headers())).json()
    assert out == {"contacts": 2, "problems": []}
    assert [c["name"] for c in (await listed(rig, workspace="sb"))["items"]] == ["Ada Lovelace", "Dev Patel"]
    rig.people.live.clear()  # Google no longer allows it
    rig.app.state.people_access._by.clear()
    bad = (await rig.client.post(f"{C}/sync", json={}, headers=headers())).json()
    assert bad["contacts"] == 0 and "no longer allows" in bad["problems"][0]
    account = (await status(rig))["accounts"][0]
    assert account["status"] == "reconnect" and "Link the account again" in account["error"]
    assert (await listed(rig, workspace="sb"))["total"] == 2  # what was read stays until it is linked again


async def test_unlinking_removes_the_contacts_and_revokes_google_only_with_the_last_link(rig):
    await link(rig, "me@x.org", [ADA], workspace="sb")
    first = (await status(rig))["accounts"][0]
    second = (await rig.client.post(f"{C}/accounts/{first['id']}/link", json={"workspace": "fsa"}, headers=headers())).json()
    assert (await rig.client.delete(f"{C}/accounts/{first['id']}", headers=headers())).status_code == 204
    assert rig.people.revoked == [] and (await listed(rig, workspace="sb"))["total"] == 0 and (await listed(rig, workspace="fsa"))["total"] == 1  # fsa still has it
    assert (await rig.client.delete(f"{C}/accounts/{second['id']}", headers=headers())).status_code == 204
    assert rig.people.revoked == ["r-me@x.org"] and (await status(rig))["accounts"] == []


async def test_contacts_belong_to_one_person(rig):
    await link(rig, "me@x.org", [ADA])
    other = headers("u2", "b@example.com")
    assert (await status(rig, other))["accounts"] == [] and (await listed(rig, other))["total"] == 0
    account = (await status(rig))["accounts"][0]
    assert (await rig.client.delete(f"{C}/accounts/{account['id']}", headers=other)).status_code == 404
    assert (await rig.client.post(f"{C}/accounts/{account['id']}/link", json={"workspace": "fsa"}, headers=other)).status_code == 404
    assert (await rig.client.get(C)).status_code == 401


async def test_a_failed_sign_in_comes_back_to_contacts_not_the_calendar(rig):
    state = await start(rig)
    denied = await rig.client.get("/v1/calendar/oauth/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert denied.status_code == 303 and "/sb/contacts?contacts_error=" in denied.headers["location"]
    bad = await rig.client.get("/v1/calendar/oauth/callback", params={"code": "nope", "state": state}, follow_redirects=False)
    assert "/sb/contacts?contacts_error=" in bad.headers["location"] and (await status(rig))["accounts"] == []
    assert (await rig.client.post(f"{C}/oauth/start", json={"workspace": "nowhere"}, headers=headers())).status_code == 422


def test_merging_keeps_workspaces_together_for_one_google_contact():
    a = ContactAccount("a1", "u1", "sb", "me@x.org", "r")
    b = ContactAccount("a2", "u1", "fsa", "me@x.org", "r")
    one, two = contact_from_person(a, ADA), contact_from_person(b, ADA)
    merged = merge_across([one, two], {"a1": "me@x.org", "a2": "me@x.org"})
    assert len(merged) == 1 and merged[0]["workspaces"] == ["fsa", "sb"] and merged[0]["accounts"] == ["me@x.org"]
