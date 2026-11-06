from .conftest import headers

S = "/v1/agent/secrets"


async def items(rig, who=None):
    return (await rig.client.get(S, headers=who or headers())).json()["items"]


async def test_a_secret_is_set_without_ever_being_read_back(rig):
    before = (await items(rig))[0]
    assert before["id"] == "deepgram" and before["name"] == "DEEPGRAM_API_KEY" and before["set"] is False
    put = await rig.client.put(f"{S}/deepgram", json={"value": "  dg-secret-value-9876  "}, headers=headers())
    assert put.status_code == 200 and put.json()["items"][0]["set"] is True
    everything = str(await items(rig)) + put.text + (await rig.client.get("/v1/agent/reminders", headers=headers())).text + (await rig.client.get("/v1/agent/settings", headers=headers())).text
    assert "dg-secret-value" not in everything and "9876" not in everything


async def test_it_is_sealed_in_the_database_and_belongs_to_one_person(rig):
    from sqlalchemy import text

    await rig.client.put(f"{S}/deepgram", json={"value": "dg-secret-value-9876"}, headers=headers())
    async with rig.app.state.session_factory() as s:
        sealed = (await s.execute(text("select keys_sealed from agent_settings"))).scalar_one()
    assert sealed and "dg-secret-value" not in sealed
    assert (await items(rig, headers("b@example.com", "u2")))[0]["set"] is False


async def test_the_connection_is_tested_against_the_stored_value(rig):
    assert (await rig.client.post(f"{S}/deepgram/test", headers=headers())).status_code == 422  # nothing set yet
    await rig.client.put(f"{S}/deepgram", json={"value": "dg-good-key"}, headers=headers())
    ok = await rig.client.post(f"{S}/deepgram/test", headers=headers())
    assert ok.status_code == 200 and ok.json() == {"ok": True, "message": "Deepgram accepted the key."}
    await rig.client.put(f"{S}/deepgram", json={"value": "bad-key"}, headers=headers())
    refused = await rig.client.post(f"{S}/deepgram/test", headers=headers())
    assert refused.status_code == 422 and "didn't accept" in refused.json()["error"]["message"]


async def test_a_secret_can_be_replaced_removed_and_only_known_names_are_kept(rig):
    await rig.client.put(f"{S}/deepgram", json={"value": "one"}, headers=headers())
    await rig.client.put(f"{S}/deepgram", json={"value": "two"}, headers=headers())
    assert (await items(rig))[0]["set"] is True
    assert (await rig.client.delete(f"{S}/deepgram", headers=headers())).json()["items"][0]["set"] is False
    assert (await rig.client.put(f"{S}/anything", json={"value": "x"}, headers=headers())).status_code == 404
    assert (await rig.client.put(f"{S}/deepgram", json={"value": "   "}, headers=headers())).status_code == 422
    assert (await rig.client.put(f"{S}/deepgram", json={"value": "two\nlines"}, headers=headers())).status_code == 422
