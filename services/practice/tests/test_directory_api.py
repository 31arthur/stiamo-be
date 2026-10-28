async def test_requires_authentication(client):
    assert (await client.get("/v1/counsel")).status_code == 401


async def test_counsel_roundtrip_is_camel_case_and_owner_is_forced(client, alice):
    body = {"kind": "advocate", "name": "  R. Iyer ", "firm": "Iyer & Partners", "proBono": True,
            "expertise": ["pow", "pow", "minimum_wages"], "userId": "someone-else"}
    r = await client.put("/v1/counsel/c1", json=body, headers=alice)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["id"] == "c1" and doc["userId"] == "alice-id"
    assert doc["name"] == "R. Iyer"
    assert doc["expertise"] == ["pow", "minimum_wages"]
    assert doc["proBono"] is True
    assert "phone" not in doc  # unset optionals are left out, like the frontend's types
    assert doc["createdAt"].endswith("Z")
    assert (await client.get("/v1/counsel/c1", headers=alice)).json() == doc


async def test_validation_errors_use_the_common_error_shape(client, alice):
    r = await client.post("/v1/counsel", json={"kind": "advocate", "name": "   "}, headers=alice)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    r = await client.post("/v1/counsel", json={"kind": "wizard", "name": "x"}, headers=alice)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    r = await client.post("/v1/clients", json={"kind": "company", "name": "Acme", "email": "nope"}, headers=alice)
    assert r.status_code == 422 and "email" in r.json()["error"]["message"].lower()


async def test_put_replaces_and_keeps_created_at(client, alice):
    first = (await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme", "phone": "1"}, headers=alice)).json()
    second = (await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme Ltd"}, headers=alice)).json()
    assert second["createdAt"] == first["createdAt"]
    assert second["name"] == "Acme Ltd" and "phone" not in second


async def test_users_are_isolated(client, alice, bob):
    await client.put("/v1/counsel/c1", json={"kind": "firm", "name": "Secret LLP"}, headers=alice)
    assert (await client.get("/v1/counsel/c1", headers=bob)).status_code == 404
    assert (await client.get("/v1/counsel", headers=bob)).json()["items"] == []
    await client.delete("/v1/counsel/c1", headers=bob)
    assert (await client.get("/v1/counsel/c1", headers=alice)).status_code == 200
    await client.put("/v1/counsel/c1", json={"kind": "firm", "name": "Bob's"}, headers=bob)
    assert (await client.get("/v1/counsel/c1", headers=alice)).json()["name"] == "Secret LLP"


async def test_pagination_and_incremental_sync(client, alice):
    for i in range(12):
        await client.put(f"/v1/counsel/c{i:02d}", json={"kind": "firm", "name": f"F{i}"}, headers=alice)
    seen, after = [], None
    while True:
        page = (await client.get("/v1/counsel", params={"limit": 5, **({"after": after} if after else {})}, headers=alice)).json()
        seen += [i["id"] for i in page["items"]]
        after = page["nextCursor"]
        if not after:
            break
    assert seen == [f"c{i:02d}" for i in range(12)]
    latest = (await client.get("/v1/counsel/c11", headers=alice)).json()["updatedAt"]
    assert (await client.get("/v1/counsel", params={"updatedAfter": latest}, headers=alice)).json()["items"] == []


async def test_client_delete_removes_its_projects(client, alice):
    await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme"}, headers=alice)
    await client.put("/v1/projects/p1", json={"title": "Site", "clientId": "k1"}, headers=alice)
    await client.put("/v1/projects/p2", json={"title": "App", "clientId": "k1"}, headers=alice)
    assert len((await client.get("/v1/clients/k1/projects", headers=alice)).json()["items"]) == 2
    assert (await client.delete("/v1/clients/k1", headers=alice)).status_code == 204
    assert (await client.get("/v1/projects/p1", headers=alice)).status_code == 404
    assert (await client.get("/v1/clients/k1", headers=alice)).status_code == 404


async def test_project_rules(client, alice):
    await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme"}, headers=alice)
    bad = {"title": "X", "clientId": "k1", "startDate": "2026-05-10", "dueDate": "2026-05-01"}
    assert (await client.put("/v1/projects/p1", json=bad, headers=alice)).status_code == 422
    assert (await client.put("/v1/projects/p1", json={"title": "X", "clientId": "ghost"}, headers=alice)).status_code in (404, 409, 422)


async def test_history_is_written_for_every_change(client, alice):
    await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme"}, headers=alice)
    await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme Ltd", "status": "past"}, headers=alice)
    items = (await client.get("/v1/activity", params={"kind": "client", "id": "k1"}, headers=alice)).json()["items"]
    assert [i["action"] for i in items] == ["updated", "created"]
    assert {c["field"] for c in items[0]["changes"]} == {"name", "status"}
    assert items[0]["changes"][0]["from"] is not None


async def test_links_notes_and_cleanup_on_delete(client, alice):
    await client.put("/v1/clients/k1", json={"kind": "company", "name": "Acme"}, headers=alice)
    await client.put("/v1/counsel/c1", json={"kind": "firm", "name": "Firm"}, headers=alice)
    a, b = {"kind": "client", "id": "k1"}, {"kind": "counsel", "id": "c1"}
    made = await client.post("/v1/links", json={"a": a, "b": b, "titles": {"a": "Acme", "b": "Firm"}}, headers=alice)
    assert made.status_code == 201
    again = await client.post("/v1/links", json={"a": b, "b": a, "titles": {"a": "Firm", "b": "Acme"}}, headers=alice)
    assert again.json()["id"] == made.json()["id"]  # undirected, no duplicates
    self_link = await client.post("/v1/links", json={"a": a, "b": a, "titles": {"a": "x", "b": "x"}}, headers=alice)
    assert self_link.status_code == 422
    assert len((await client.get("/v1/links", params={"kind": "client", "id": "k1"}, headers=alice)).json()["items"]) == 1

    await client.post("/v1/activity/notes", json={"ref": a, "title": "Acme", "text": "Called them"}, headers=alice)
    actions = [i["action"] for i in (await client.get("/v1/activity", params={"kind": "client", "id": "k1"}, headers=alice)).json()["items"]]
    assert "note" in actions and "linked" in actions

    await client.delete("/v1/clients/k1", headers=alice)
    assert (await client.get("/v1/links", params={"kind": "counsel", "id": "c1"}, headers=alice)).json()["items"] == []
