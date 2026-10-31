import asyncio

from app.application.accounts import MailService
from app.infrastructure.db.repositories import SqlAccountRepository

from tests.conftest import NEW, headers


async def account(client) -> str:
    return (await client.post("/v1/mail/accounts", json=NEW, headers=headers())).json()["id"]


async def domain_account(client, mail, account_id):
    async with client.app.state.session_factory() as s:
        return await MailService(SqlAccountRepository(s, client.app.state.box), mail, client.app.state.live)._account("u1", account_id)


async def test_folders(client):
    a = await account(client)
    f = (await client.get(f"/v1/mail/accounts/{a}/folders", headers=headers())).json()
    assert [x["role"] for x in f] == ["inbox", "sent"] and f[0]["unseen"] == 2


async def test_pages_run_back_in_time(client):
    a = await account(client)
    first = (await client.get(f"/v1/mail/accounts/{a}/messages?limit=2", headers=headers())).json()
    assert [m["uid"] for m in first["messages"]] == [3, 2] and first["nextBefore"] == 2 and first["total"] == 3
    assert first["messages"][0]["sender"] == {"name": "Ada", "address": "ada@example.com"} and first["messages"][0]["hasAttachments"] is True
    last = (await client.get(f"/v1/mail/accounts/{a}/messages?limit=2&before=2", headers=headers())).json()
    assert [m["uid"] for m in last["messages"]] == [1] and last["nextBefore"] is None


async def test_search(client):
    a = await account(client)
    found = (await client.get(f"/v1/mail/accounts/{a}/messages?q=Subject 2", headers=headers())).json()
    assert [m["uid"] for m in found["messages"]] == [2]


async def test_message_and_marking(client):
    a = await account(client)
    m = (await client.get(f"/v1/mail/accounts/{a}/messages/3", headers=headers())).json()
    assert m["seen"] is False and m["attachments"][0]["filename"] == "brief.pdf" and "<b>there</b>" in m["html"]
    r = await client.patch(f"/v1/mail/accounts/{a}/messages/3", json={"seen": True, "flagged": True}, headers=headers())
    assert r.status_code == 204
    m = (await client.get(f"/v1/mail/accounts/{a}/messages/3", headers=headers())).json()
    assert m["seen"] is True and m["flagged"] is True
    assert (await client.patch(f"/v1/mail/accounts/{a}/messages/3", json={}, headers=headers())).status_code == 422


async def test_server_trouble_is_reported_not_crashed(client):
    a = await account(client)
    r = await client.get(f"/v1/mail/accounts/{a}/messages?folder=Nope", headers=headers())
    assert r.status_code == 502 and "folder" in r.json()["error"]["message"]
    assert (await client.get(f"/v1/mail/accounts/{a}/messages/99", headers=headers())).status_code == 502


async def test_attachments_are_always_downloads(client):
    a = await account(client)
    r = await client.get(f"/v1/mail/accounts/{a}/messages/3/attachments/0", headers=headers())
    assert r.content == b"%PDF" and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment; filename*=UTF-8''brief%20%C3%BC.pdf")
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_live_hub_shares_one_connection_and_pushes_changes(client, mail):
    a = await account(client)
    hub = client.app.state.live
    acct = await domain_account(client, mail, a)

    async with hub.listen(acct, "INBOX") as one, hub.listen(acct, "INBOX") as two:
        assert (await asyncio.wait_for(one.get(), 2)) == {"type": "state", "state": "connecting"}
        assert (await asyncio.wait_for(one.get(), 2)) == {"type": "state", "state": "live"}
        assert mail.idling == 1 and hub.watching() == 1  # two viewers, one IDLE connection
        mail.pokes[0]()  # the server pushed "EXISTS": a new message
        assert (await asyncio.wait_for(one.get(), 2)) == {"type": "changed"}
        await two.get()
        await two.get()
        assert (await asyncio.wait_for(two.get(), 2)) == {"type": "changed"}
    assert hub.watching() == 0  # the last viewer left; the connection is told to close
    await asyncio.sleep(0.3)
    assert mail.idling == 0


async def test_removing_a_mailbox_ends_its_feeds(client, mail):
    a = await account(client)
    hub = client.app.state.live
    acct = await domain_account(client, mail, a)
    async with hub.listen(acct, "INBOX") as q:
        await client.delete(f"/v1/mail/accounts/{a}", headers=headers())
        events = []
        while not events or events[-1] is not None:
            events.append(await asyncio.wait_for(q.get(), 2))
        assert events[-1] is None
