import pytest
from app.application.ports import FilesClient
from app.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from stiamo_common.db import Base

from tests.conftest import make_token


class RecordingFiles(FilesClient):
    def __init__(self, token: str) -> None:
        self.token, self.calls = token, []

    async def delete_owner(self, ref):
        self.calls.append(("delete_owner", ref.kind, ref.id, self.token))

    async def transfer(self, source, target):
        self.calls.append(("transfer", source.id, target.id))


@pytest.fixture
async def spy_client():
    calls: list[tuple] = []

    def factory(token: str) -> FilesClient:
        client = RecordingFiles(token)
        client.calls = calls
        return client

    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:"), files_factory=factory)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, calls


async def test_deleting_a_record_clears_its_attachments_using_the_callers_token(spy_client, alice):
    client, calls = spy_client
    await client.put("/v1/tasks/t1", json={"title": "x"}, headers=alice)
    await client.delete("/v1/tasks/t1", headers=alice)
    assert calls == [("delete_owner", "task", "t1", alice["Authorization"].split()[1])]


async def test_merging_hearings_moves_their_attachments(spy_client, alice):
    client, calls = spy_client
    for cid in ("c1", "c2"):
        await client.put(f"/v1/cases/{cid}", json={"organization": "SA", "title": cid}, headers=alice)
    await client.put("/v1/hearings/h1", json={"caseId": "c1", "date": "2026-03-10", "purpose": "x"}, headers=alice)
    await client.put("/v1/hearings/h2", json={"caseId": "c2", "date": "2026-03-10", "purpose": "x"}, headers=alice)
    calls.clear()
    await client.put("/v1/hearings/h1/cases", json={"caseIds": ["c2"]}, headers=alice)
    assert ("transfer", "h2", "h1") in calls
    assert ("delete_owner", "hearing", "h2", alice["Authorization"].split()[1]) in calls  # after the move: nothing left to delete


async def test_the_files_service_can_write_history_lines(client, alice, bob):
    ref = {"kind": "case", "id": "c1"}
    r = await client.post("/v1/activity/events", headers=alice, json={
        "ref": ref, "title": "SA-2026-001", "action": "attached", "summary": "Attached “order.pdf” (1 KB)"})
    assert r.status_code == 204
    items = (await client.get("/v1/activity", params={"kind": "case", "id": "c1"}, headers=alice)).json()["items"]
    assert [(i["action"], i["summary"]) for i in items] == [("attached", "Attached “order.pdf” (1 KB)")]
    assert (await client.get("/v1/activity", params={"kind": "case", "id": "c1"}, headers=bob)).json()["items"] == []
    # only the two document events are accepted from outside
    bad = await client.post("/v1/activity/events", headers=alice, json={"ref": ref, "action": "deleted", "summary": "x"})
    assert bad.status_code == 422
    assert make_token("x")
