import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import FakeGateway, build, doc

ID = "1AbCdEfGhIjKlMnOpQrStUv"


@pytest.fixture
def files():
    return FakeGateway([doc(ID, "Order.pdf")])


async def get(gateway, path, headers, allowed=("owner@example.com",)):
    async with AsyncClient(transport=ASGITransport(app=build(gateway, allowed)), base_url="http://test") as c:
        return await c.get(path, headers=headers)


async def test_streams_the_file_to_the_owner(files, owner):
    r = await get(files, f"/v1/drive/files/{ID}/content", owner)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-bytes-of-" + ID.encode()


async def test_needs_a_sign_in(files):
    assert (await get(files, f"/v1/drive/files/{ID}/content", {})).status_code == 401


async def test_only_for_listed_emails(files, stranger):
    assert (await get(files, f"/v1/drive/files/{ID}/content", stranger)).status_code == 403


async def test_unknown_file_is_not_found(files, owner):
    assert (await get(files, "/v1/drive/files/0123456789abcdef/content", owner)).status_code == 404


async def test_rejects_ids_that_are_not_file_ids(files, owner):
    assert (await get(files, "/v1/drive/files/short/content", owner)).status_code == 404


async def test_says_when_google_is_down(owner):
    r = await get(FakeGateway([doc(ID, "x.pdf")], fail="down"), f"/v1/drive/files/{ID}/content", owner)
    assert r.status_code == 502
