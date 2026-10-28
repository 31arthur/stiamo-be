import time

import jwt
import pytest
from app.config import Settings
from app.domain.attachment import default_link_name, describe_link, format_bytes, is_inline_safe

GDOC = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit?usp=sharing"


async def upload(client, headers, name="order.pdf", data=b"%PDF-1.4 hello", mime="application/pdf", kind="hearing", id="h1",
                 title="Hearing: Arguments"):
    return await client.post("/v1/attachments", headers=headers, files={"file": (name, data, mime)},
                             data={"ownerKind": kind, "ownerId": id, "ownerTitle": title})


# --- links (pure) --------------------------------------------------------------------------------------------


def test_google_documents_get_an_embeddable_address():
    link = describe_link(GDOC)
    assert link.provider == "google-doc"
    assert link.embed_url == "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/preview"
    sheet = describe_link("https://docs.google.com/spreadsheets/u/0/d/1AbCdEfGhIjKlMnOpQrStUv/edit#gid=0")
    assert sheet.provider == "google-sheet" and sheet.embed_url.endswith("/preview")
    assert describe_link("https://docs.google.com/forms/d/e/1AbCdEfGhIjKlMnOpQrStUv/viewform").embed_url is None
    folder = describe_link("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv")
    assert folder.provider == "google-folder" and "embeddedfolderview?id=1AbCdEfGhIjKlMnOpQrStUv" in folder.embed_url
    opened = describe_link("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUv")
    assert opened.provider == "google-drive" and opened.embed_url.endswith("/file/d/1AbCdEfGhIjKlMnOpQrStUv/preview")


def test_other_addresses():
    assert describe_link("https://example.com/files/order.pdf").embed_url == "https://example.com/files/order.pdf"
    assert describe_link("http://example.com/page").embed_url is None
    assert describe_link("http://example.com/x.pdf").embed_url is None  # only https documents are framed
    for bad in ("", "not a url", "ftp://example.com/x", "javascript:alert(1)", "https://"):
        assert describe_link(bad) is None


def test_names_for_links_and_sizes():
    assert default_link_name(describe_link(GDOC)) == "Google Doc"
    assert default_link_name(describe_link("https://www.example.com/")) == "example.com"
    assert default_link_name(describe_link("https://example.com/a/Order%20Sheet.pdf")) == "Order Sheet.pdf"
    assert format_bytes(10) == "1 KB" and format_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert is_inline_safe("image/png") and is_inline_safe("application/pdf")
    assert not is_inline_safe("text/html") and not is_inline_safe("image/svg+xml")


# --- uploading -----------------------------------------------------------------------------------------------


async def test_requires_authentication(client):
    assert (await client.get("/v1/attachments")).status_code == 401


async def test_upload_store_and_download(client, alice, storage, history):
    r = await upload(client, alice)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["owner"] == {"kind": "hearing", "id": "h1"} and a["name"] == "order.pdf"
    assert a["size"] == 14 and a["backend"] == "storage" and a["mime"] == "application/pdf"
    assert storage.files[a["path"]] == b"%PDF-1.4 hello" and a["path"].startswith("alice-id/")

    got = await client.get(f"/v1/attachments/{a['id']}/content", headers=alice)
    assert got.content == b"%PDF-1.4 hello" and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("inline")
    assert got.headers["x-content-type-options"] == "nosniff"

    token, kind, id_, title, action, summary = history.calls[0]
    assert token == alice["Authorization"].split()[1] and (kind, id_, action) == ("hearing", "h1", "attached")
    assert title == "Hearing: Arguments" and "order.pdf" in summary and "1 KB" in summary


async def test_size_and_emptiness_limits(client, alice, storage):
    assert (await upload(client, alice, data=b"")).status_code == 422
    big = await upload(client, alice, data=b"x" * 1025)
    assert big.status_code == 422 and "limit" in big.json()["error"]["message"]
    assert (await upload(client, alice, data=b"x" * 1024)).status_code == 201
    assert len(storage.files) == 1


async def test_active_content_is_never_shown_inline(client, alice):
    a = (await upload(client, alice, name="x.html", data=b"<script>alert(1)</script>", mime="text/html")).json()
    r = await client.get(f"/v1/attachments/{a['id']}/content", headers=alice)
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-security-policy"] == "sandbox"


async def test_unknown_owner_kind_is_rejected(client, alice):
    assert (await upload(client, alice, kind="invoice")).status_code == 422


# --- links ---------------------------------------------------------------------------------------------------


async def test_attach_a_link(client, alice, storage, history):
    r = await client.post("/v1/attachments/links", headers=alice, json={
        "owner": {"kind": "case", "id": "c1"}, "ownerTitle": "SA-2026-001", "url": GDOC})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["backend"] == "link" and a["name"] == "Google Doc" and a["mime"] == "text/uri-list" and a["size"] == 0
    assert a["link"]["provider"] == "google-doc" and a["link"]["embedUrl"].endswith("/preview") and "path" not in a
    assert storage.files == {}
    assert history.calls[0][4:] == ("attached", "Linked “Google Doc”")
    named = (await client.post("/v1/attachments/links", headers=alice, json={
        "owner": {"kind": "case", "id": "c1"}, "url": "https://example.com/x", "name": "  Fee note "})).json()
    assert named["name"] == "Fee note"
    bad = await client.post("/v1/attachments/links", headers=alice, json={"owner": {"kind": "case", "id": "c1"}, "url": "nope"})
    assert bad.status_code == 422
    no_file = await client.get(f"/v1/attachments/{a['id']}/content", headers=alice)
    assert no_file.status_code == 422


# --- reading, renaming, deleting --------------------------------------------------------------------------------


async def test_listing_by_record_and_paging(client, alice):
    for i in range(5):
        await upload(client, alice, name=f"f{i}.txt", data=b"x", mime="text/plain", id="h1" if i < 3 else "h2")
    mine = (await client.get("/v1/attachments", params={"kind": "hearing", "id": "h1"}, headers=alice)).json()
    assert len(mine["items"]) == 3 and mine["nextCursor"] is None
    page = (await client.get("/v1/attachments", params={"limit": 2}, headers=alice)).json()
    assert len(page["items"]) == 2 and page["nextCursor"]
    rest = (await client.get("/v1/attachments", params={"limit": 10, "after": page["nextCursor"]}, headers=alice)).json()
    assert len(rest["items"]) == 3
    assert (await client.get("/v1/attachments", params={"kind": "hearing"}, headers=alice)).status_code == 422


async def test_caption_and_delete(client, alice, storage, history):
    a = (await upload(client, alice)).json()
    r = await client.patch(f"/v1/attachments/{a['id']}", json={"caption": "  Interim order "}, headers=alice)
    assert r.json()["caption"] == "Interim order"
    cleared = await client.patch(f"/v1/attachments/{a['id']}", json={"caption": " "}, headers=alice)
    assert "caption" not in cleared.json()

    assert (await client.delete(f"/v1/attachments/{a['id']}", params={"ownerTitle": "Hearing"}, headers=alice)).status_code == 204
    assert storage.files == {} and (await client.get(f"/v1/attachments/{a['id']}", headers=alice)).status_code == 404
    assert history.calls[-1][4:] == ("detached", "Removed “order.pdf”")
    assert (await client.delete(f"/v1/attachments/{a['id']}", headers=alice)).status_code == 204  # idempotent


async def test_users_cannot_see_or_touch_each_others_files(client, alice, bob, storage):
    a = (await upload(client, alice)).json()
    assert (await client.get(f"/v1/attachments/{a['id']}", headers=bob)).status_code == 404
    assert (await client.get(f"/v1/attachments/{a['id']}/content", headers=bob)).status_code == 404
    assert (await client.post(f"/v1/attachments/{a['id']}/url", headers=bob)).status_code == 404
    assert (await client.get("/v1/attachments", headers=bob)).json()["items"] == []
    await client.delete(f"/v1/attachments/{a['id']}", headers=bob)
    assert len(storage.files) == 1


# --- short-lived addresses -----------------------------------------------------------------------------------------


async def test_signed_address_works_without_a_header_and_expires(client, alice):
    a = (await upload(client, alice)).json()
    shared = (await client.post(f"/v1/attachments/{a['id']}/url", headers=alice)).json()
    assert shared["expiresIn"] == 300 and shared["url"].startswith("/v1/files/")
    assert (await client.get(shared["url"])).content == b"%PDF-1.4 hello"

    s = Settings()
    expired = jwt.encode({"typ": "file", "usr": "alice-id", "att": a["id"], "exp": int(time.time()) - 5}, s.signing_secret, "HS256")
    assert (await client.get(f"/v1/files/{expired}")).status_code == 401
    forged = jwt.encode({"typ": "file", "usr": "alice-id", "att": a["id"], "exp": int(time.time()) + 60}, "x" * 40, "HS256")
    assert (await client.get(f"/v1/files/{forged}")).status_code == 401
    other_type = jwt.encode({"typ": "access", "usr": "alice-id", "att": a["id"], "exp": int(time.time()) + 60}, s.signing_secret, "HS256")
    assert (await client.get(f"/v1/files/{other_type}")).status_code == 401
    assert (await client.get("/v1/files/garbage")).status_code == 401


async def test_a_link_attachment_hands_back_its_own_address(client, alice):
    a = (await client.post("/v1/attachments/links", headers=alice, json={"owner": {"kind": "case", "id": "c1"}, "url": GDOC})).json()
    assert (await client.post(f"/v1/attachments/{a['id']}/url", headers=alice)).json()["url"] == GDOC


# --- called by the practice service ------------------------------------------------------------------------------------


async def test_merging_records_moves_their_files(client, alice):
    await upload(client, alice, id="h2")
    r = await client.post("/v1/attachments/transfer", headers=alice, json={
        "source": {"kind": "hearing", "id": "h2"}, "target": {"kind": "hearing", "id": "h1"}})
    assert r.json() == {"moved": 1}
    assert len((await client.get("/v1/attachments", params={"kind": "hearing", "id": "h1"}, headers=alice)).json()["items"]) == 1
    assert (await client.get("/v1/attachments", params={"kind": "hearing", "id": "h2"}, headers=alice)).json()["items"] == []


async def test_deleting_a_record_removes_its_files(client, alice, bob, storage):
    await upload(client, alice, id="h1")
    await upload(client, alice, id="h1", name="b.pdf")
    await upload(client, alice, id="keep")
    await upload(client, bob, id="h1")
    r = await client.delete("/v1/attachments", params={"kind": "hearing", "id": "h1"}, headers=alice)
    assert r.json() == {"removed": 2}
    assert len(storage.files) == 2  # alice's other record and bob's file are untouched
    assert len((await client.get("/v1/attachments", headers=alice)).json()["items"]) == 1
    assert len((await client.get("/v1/attachments", headers=bob)).json()["items"]) == 1


@pytest.mark.parametrize("path", ["../escape", "a/../../escape"])
async def test_local_storage_cannot_escape_its_directory(tmp_path, path):
    from app.infrastructure.storage import LocalFileStorage

    store = LocalFileStorage(str(tmp_path / "root"))
    with pytest.raises(ValueError):
        await store.put(path, b"x")
    await store.put("u/1", b"ok")
    assert await store.read("u/1") == b"ok"
    await store.delete("u/1")
    await store.delete("u/1")  # idempotent
