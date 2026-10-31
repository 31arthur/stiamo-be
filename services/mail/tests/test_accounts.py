from tests.conftest import NEW, headers


async def connect(client, **over):
    return await client.post("/v1/mail/accounts", json={**NEW, **over}, headers=headers())


async def test_requires_sign_in(client):
    assert (await client.get("/v1/mail/accounts")).status_code == 401


async def test_connect_checks_the_server_and_never_returns_the_password(client, mail):
    r = await connect(client)
    assert r.status_code == 201
    body = r.json()
    assert body["workspace"] == "sb" and body["host"] == "imap.example.com" and body["label"] == "Chambers"
    assert "password" not in body and "s3cret" not in r.text
    assert mail.checked[-1].password == "s3cret"
    assert (await client.get("/v1/mail/accounts", headers=headers())).json()[0]["id"] == body["id"]


async def test_password_is_sealed_in_the_database(client):
    from app.infrastructure.db.models import AccountModel
    from sqlalchemy import select

    await connect(client)
    async with client.app.state.session_factory() as s:
        row = (await s.scalars(select(AccountModel))).one()
    assert row.password_sealed and "s3cret" not in row.password_sealed


async def test_a_wrong_password_is_refused_with_the_servers_reason(client):
    r = await connect(client, password="wrong")
    assert r.status_code == 422 and r.json()["error"]["code"] == "mail_auth_failed"
    assert (await client.get("/v1/mail/accounts", headers=headers())).json() == []


async def test_an_unreachable_server_is_a_bad_gateway(client):
    r = await connect(client, host="down.example.com")
    assert r.status_code == 502 and r.json()["error"]["code"] == "mail_unavailable"


async def test_validation(client):
    assert (await connect(client, workspace="nowhere")).status_code == 422
    assert (await connect(client, security="plain")).status_code == 422
    assert (await connect(client, host="not a host!")).status_code == 422
    assert (await connect(client, address="nope")).status_code == 422
    assert (await connect(client, port=0)).status_code == 422


async def test_same_mailbox_twice_in_a_workspace_is_refused_but_other_workspaces_are_fine(client):
    assert (await connect(client)).status_code == 201
    assert (await connect(client)).status_code == 422
    assert (await connect(client, workspace="fsa")).status_code == 201


async def test_list_by_workspace_and_isolation_between_people(client):
    a = (await connect(client)).json()
    await connect(client, workspace="fsa", address="a@fsa.example.com", username="a@fsa.example.com")
    assert len((await client.get("/v1/mail/accounts?workspace=sb", headers=headers())).json()) == 1
    other = headers("u2", "other@example.com")
    assert (await client.get("/v1/mail/accounts", headers=other)).json() == []
    for path in ("folders", "messages", "events"):
        assert (await client.get(f"/v1/mail/accounts/{a['id']}/{path}", headers=other)).status_code == 404
    assert (await client.delete(f"/v1/mail/accounts/{a['id']}", headers=other)).status_code == 404
    assert (await client.patch(f"/v1/mail/accounts/{a['id']}", json={"label": "x"}, headers=other)).status_code == 404


async def test_edit_keeps_the_password_unless_a_new_one_is_given(client, mail):
    a = (await connect(client)).json()
    r = await client.patch(f"/v1/mail/accounts/{a['id']}", json={"label": "Main", "workspace": "fsa"}, headers=headers())
    assert r.json()["label"] == "Main" and r.json()["workspace"] == "fsa"
    assert len(mail.checked) == 1  # nothing about the connection changed, so the server wasn't asked again
    r = await client.patch(f"/v1/mail/accounts/{a['id']}", json={"password": "wrong"}, headers=headers())
    assert r.status_code == 422
    await client.patch(f"/v1/mail/accounts/{a['id']}", json={"password": "fresh"}, headers=headers())
    assert mail.checked[-1].password == "fresh"


async def test_disconnect(client):
    a = (await connect(client)).json()
    assert (await client.delete(f"/v1/mail/accounts/{a['id']}", headers=headers())).status_code == 204
    assert (await client.get("/v1/mail/accounts", headers=headers())).json() == []
