from tests.conftest import make_token

URL = "/v1/collections/tasks"


async def test_requires_authentication(client):
    assert (await client.get(URL)).status_code == 401
    refresh = {"Authorization": f"Bearer {make_token('u', typ='refresh')}"}
    assert (await client.get(URL, headers=refresh)).status_code == 401
    expired = {"Authorization": f"Bearer {make_token('u', ttl=-3600)}"}
    assert (await client.get(URL, headers=expired)).status_code == 401


async def test_put_get_roundtrip_and_ownership_is_forced(client, alice):
    doc = {"id": "t1", "userId": "someone-else", "title": "Call", "nested": {"a": [1, 2]}}
    r = await client.put(f"{URL}/t1", json=doc, headers=alice)
    assert r.status_code == 200
    assert r.json()["userId"] == "alice-id"
    got = (await client.get(f"{URL}/t1", headers=alice)).json()
    assert got["title"] == "Call" and got["nested"] == {"a": [1, 2]}


async def test_put_replaces_document(client, alice):
    await client.put(f"{URL}/t1", json={"title": "A", "extra": 1}, headers=alice)
    await client.put(f"{URL}/t1", json={"title": "B"}, headers=alice)
    got = (await client.get(f"{URL}/t1", headers=alice)).json()
    assert got["title"] == "B" and "extra" not in got
    assert len((await client.get(URL, headers=alice)).json()["items"]) == 1


async def test_users_cannot_see_or_touch_each_others_records(client, alice, bob):
    await client.put(f"{URL}/t1", json={"title": "secret"}, headers=alice)
    assert (await client.get(f"{URL}/t1", headers=bob)).status_code == 404
    assert (await client.get(URL, headers=bob)).json()["items"] == []
    await client.delete(f"{URL}/t1", headers=bob)
    assert (await client.get(f"{URL}/t1", headers=alice)).status_code == 200
    # same id for another user is a different record
    await client.put(f"{URL}/t1", json={"title": "bob's"}, headers=bob)
    assert (await client.get(f"{URL}/t1", headers=alice)).json()["title"] == "secret"


async def test_collections_are_separate_and_validated(client, alice):
    await client.put(f"{URL}/x", json={"a": 1}, headers=alice)
    assert (await client.get("/v1/collections/meetings/x", headers=alice)).status_code == 404
    assert (await client.get("/v1/collections/nonsense", headers=alice)).status_code == 404


async def test_id_mismatch_rejected(client, alice):
    r = await client.put(f"{URL}/a", json={"id": "b"}, headers=alice)
    assert r.status_code == 422


async def test_delete_is_idempotent(client, alice):
    await client.put(f"{URL}/t1", json={}, headers=alice)
    assert (await client.delete(f"{URL}/t1", headers=alice)).status_code == 204
    assert (await client.delete(f"{URL}/t1", headers=alice)).status_code == 204
    assert (await client.get(f"{URL}/t1", headers=alice)).status_code == 404


async def test_batch_and_pagination(client, alice):
    items = [{"id": f"t{i:03d}", "n": i} for i in range(25)]
    r = await client.post(f"{URL}/batch", json={"items": items}, headers=alice)
    assert r.json() == {"saved": 25}
    seen, after = [], None
    while True:
        params = {"limit": 10, **({"after": after} if after else {})}
        page = (await client.get(URL, params=params, headers=alice)).json()
        seen += [i["id"] for i in page["items"]]
        after = page["next_cursor"]
        if not after:
            break
    assert seen == sorted(i["id"] for i in items)


async def test_batch_requires_ids_and_respects_limit(client, alice):
    assert (await client.post(f"{URL}/batch", json={"items": [{"n": 1}]}, headers=alice)).status_code == 422
    too_many = [{"id": str(i)} for i in range(501)]
    assert (await client.post(f"{URL}/batch", json={"items": too_many}, headers=alice)).status_code == 422


async def test_oversized_document_rejected(client, alice):
    r = await client.put(f"{URL}/big", json={"blob": "x" * 1_100_000}, headers=alice)
    assert r.status_code == 422
