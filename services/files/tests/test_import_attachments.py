from app.infrastructure.db.repository import SqlAttachmentRepository
from app.tools.import_attachments import import_attachments


def row(user, **doc):
    return {"userId": user, "data": doc}


async def test_files_and_links_come_across(app, client, alice, storage, tmp_path):
    src = tmp_path / "alice-id" / "att1"
    src.mkdir(parents=True)
    (src / "order.pdf").write_bytes(b"%PDF-1.4 old")
    rows = [
        row("alice-id", id="att1", owner={"kind": "hearing", "id": "h1"}, name="order.pdf", mime="application/pdf", size=1,
            backend="storage", path="alice-id/att1/order.pdf", createdAt="2026-01-05T10:00:00.000Z", caption="Interim order"),
        row("alice-id", id="att2", owner={"kind": "case", "id": "c1"}, name="Fee note", mime="text/uri-list", size=0, backend="link",
            link={"url": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit", "provider": "google-doc",
                  "embedUrl": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/preview"}),
        row("alice-id", id="att3", owner={"kind": "case", "id": "c1"}, name="gone.pdf", backend="storage", path="alice-id/att3/gone.pdf"),
        row("alice-id", id="att4", owner={"kind": "case", "id": "c1"}, name="offline.pdf", backend="local"),
        row("alice-id", id="att5", owner={"kind": "invoice", "id": "x"}, name="odd.pdf", backend="link", link={"url": "https://x.co", "provider": "web"}),
    ]
    async with app.state.session_factory() as session:
        report = await import_attachments(SqlAttachmentRepository(session), storage, rows, tmp_path)
        await session.commit()
    assert (report.files, report.links, len(report.skipped)) == (1, 1, 3)
    assert any("gone.pdf" in s and "not found" in s for s in report.skipped)

    mine = {a["id"]: a for a in (await client.get("/v1/attachments", headers=alice)).json()["items"]}
    assert set(mine) == {"att1", "att2"}
    assert mine["att1"]["size"] == 12 and mine["att1"]["caption"] == "Interim order" and mine["att1"]["createdAt"] == "2026-01-05T10:00:00.000Z"
    assert (await client.get("/v1/attachments/att1/content", headers=alice)).content == b"%PDF-1.4 old"
    assert mine["att2"]["link"]["provider"] == "google-doc"


async def test_dry_run_and_repeat(app, client, alice, storage, tmp_path):
    (tmp_path / "u" / "a").mkdir(parents=True)
    (tmp_path / "u" / "a" / "f.txt").write_bytes(b"hi")
    rows = [row("alice-id", id="a", owner={"kind": "task", "id": "t"}, name="f.txt", mime="text/plain", backend="storage", path="u/a/f.txt")]

    async def run(dry):
        async with app.state.session_factory() as session:
            r = await import_attachments(SqlAttachmentRepository(session), storage, rows, tmp_path, dry)
            await session.commit()
            return r

    assert (await run(True)).files == 1 and storage.files == {}
    await run(False)
    await run(False)
    assert len((await client.get("/v1/attachments", headers=alice)).json()["items"]) == 1
