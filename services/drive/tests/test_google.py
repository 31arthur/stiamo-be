import json
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from app.application.ports import DriveUnavailable, NotConnected
from app.infrastructure.google import GoogleDriveGateway, ServiceAccountTokens, load_credentials
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SCOPE = "https://www.googleapis.com/auth/drive.readonly"


@pytest.fixture(scope="module")
def key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return pem, private.public_key()


def credentials(pem):
    return {"client_email": "reader@project.iam.gserviceaccount.com", "private_key": pem, "token_uri": "https://oauth2.test/token"}


class FakeGoogle:
    """Plays Google: the token endpoint and the Drive API."""

    def __init__(self, files=None, token_status=200, drive_status=200):
        self.token_calls, self.drive_calls = [], []
        self.files, self.token_status, self.drive_status = files or [], token_status, drive_status
        self.clock = 1_000.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.test":
            self.token_calls.append(parse_qs(request.content.decode()))
            if self.token_status != 200:
                return httpx.Response(self.token_status, text="invalid_grant")
            return httpx.Response(200, json={"access_token": f"tok-{len(self.token_calls)}", "expires_in": 3600})
        self.drive_calls.append(request)
        if self.drive_status != 200:
            return httpx.Response(self.drive_status, text="denied")
        if request.url.path.endswith("/files/folder-1"):
            return httpx.Response(200, json={"name": "Letters"})
        return httpx.Response(200, json={"files": self.files, "nextPageToken": "more"})


def gateway(google: FakeGoogle, pem, now=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(google.handler))
    tokens = ServiceAccountTokens(credentials(pem), client, SCOPE, now=now or (lambda: google.clock))
    return GoogleDriveGateway(tokens, client), tokens


async def test_signs_in_as_the_service_account_and_asks_for_read_only_access(key):
    pem, public = key
    google = FakeGoogle()
    gw, _ = gateway(google, pem)
    await gw.search("q", limit=5)
    form = google.token_calls[0]
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = jwt.decode(form["assertion"][0], public, algorithms=["RS256"], audience="https://oauth2.test/token",
                        options={"verify_exp": False, "verify_iat": False})  # the fake clock is far in the past
    assert claims["iss"] == "reader@project.iam.gserviceaccount.com" and claims["scope"] == SCOPE
    assert claims["exp"] - claims["iat"] == 3600
    assert google.drive_calls[0].headers["Authorization"] == "Bearer tok-1"


async def test_the_token_is_reused_and_renewed_before_it_expires(key):
    pem, _ = key
    google = FakeGoogle()
    gw, _ = gateway(google, pem)
    await gw.search("a", limit=5)
    await gw.search("b", limit=5)
    assert len(google.token_calls) == 1
    google.clock += 3600 - 30  # inside the last minute: renewed early
    await gw.search("c", limit=5)
    assert len(google.token_calls) == 2 and google.drive_calls[-1].headers["Authorization"] == "Bearer tok-2"


async def test_searches_at_once_share_one_sign_in(key):
    import asyncio

    pem, _ = key
    google = FakeGoogle()
    gw, _ = gateway(google, pem)
    await asyncio.gather(*[gw.search(str(i), limit=5) for i in range(6)])
    assert len(google.token_calls) == 1


async def test_what_is_asked_of_drive_and_what_comes_back(key):
    pem, _ = key
    google = FakeGoogle(files=[{"id": "f1", "name": "Letter.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-01-01T00:00:00.000Z",
                                "webViewLink": "https://drive.google.com/file/d/f1/view", "size": "2048"},
                               {"id": "f2", "name": "Notes", "mimeType": "application/vnd.google-apps.document"}])
    gw, _ = gateway(google, pem)
    page = await gw.search("fullText contains 'x'", limit=500, page_token="t", newest_first=True)
    params = dict(google.drive_calls[0].url.params)
    assert params["q"] == "fullText contains 'x'" and params["pageSize"] == "50" and params["pageToken"] == "t"
    assert params["orderBy"] == "modifiedTime desc" and params["supportsAllDrives"] == "true"
    assert "id,name,mimeType" in params["fields"]
    assert [f.name for f in page.files] == ["Letter.pdf", "Notes"] and page.files[0].size == 2048 and page.files[1].size is None
    assert page.next_page_token == "more"
    await gw.search("q", limit=5)
    assert "orderBy" not in dict(google.drive_calls[1].url.params)  # content searches keep Google's own ordering


async def test_failures_become_one_error_that_hides_googles_details(key):
    pem, _ = key
    for kwargs in ({"token_status": 400}, {"drive_status": 403}, {"drive_status": 500}):
        gw, _ = gateway(FakeGoogle(**kwargs), pem)
        with pytest.raises(DriveUnavailable):
            await gw.search("q", limit=5)

    def boom(request):
        raise httpx.ConnectError("no route")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    with pytest.raises(DriveUnavailable):
        await GoogleDriveGateway(ServiceAccountTokens(credentials(pem), client, SCOPE), client).search("q", limit=5)


async def test_without_credentials_nothing_is_attempted():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("no request expected")))
    gw = GoogleDriveGateway(None, client)
    assert gw.configured is False
    with pytest.raises(NotConnected):
        await gw.search("q", limit=5)
    assert await gw.folder_name("x") is None


async def test_folder_name(key):
    pem, _ = key
    gw, _ = gateway(FakeGoogle(), pem)
    assert await gw.folder_name("folder-1") == "Letters"
    gw, _ = gateway(FakeGoogle(drive_status=404), pem)
    assert await gw.folder_name("folder-1") is None


def test_credentials_come_from_a_file_or_the_environment(tmp_path, key):
    pem, _ = key
    text = json.dumps({**credentials(pem), "type": "service_account"})
    path = tmp_path / "sa.json"
    path.write_text(text, encoding="utf-8")
    assert load_credentials(file=str(path))["client_email"].startswith("reader@")
    assert load_credentials(inline=text)["private_key"] == pem
    assert load_credentials() is None and load_credentials(file=str(tmp_path / "missing.json")) is None
    with pytest.raises(ValueError):
        load_credentials(inline='{"client_email": "x"}')
