from tests.conftest import ALICE


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_register_returns_user_and_tokens(alice):
    assert alice["user"]["email"] == "alice@example.com"
    assert alice["tokens"]["token_type"] == "bearer"
    assert alice["tokens"]["expires_in"] == 900
    assert "password" not in str(alice).lower() or "password_hash" not in alice["user"]


async def test_duplicate_email_conflicts_case_insensitively(client, alice):
    r = await client.post("/v1/auth/register", json={**ALICE, "email": "ALICE@example.com"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_short_password_rejected(client):
    r = await client.post("/v1/auth/register", json={**ALICE, "password": "short"})
    assert r.status_code == 422


async def test_login_ok_and_wrong_password_and_unknown_email(client, alice):
    ok = await client.post("/v1/auth/login", json={"email": "alice@example.com", "password": ALICE["password"]})
    assert ok.status_code == 200
    bad = await client.post("/v1/auth/login", json={"email": "alice@example.com", "password": "nope-nope"})
    unknown = await client.post("/v1/auth/login", json={"email": "who@example.com", "password": "nope-nope"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()  # no account enumeration


async def test_me_requires_valid_access_token(client, alice):
    assert (await client.get("/v1/users/me")).status_code == 401
    assert (await client.get("/v1/users/me", headers=bearer("garbage"))).status_code == 401
    r = await client.get("/v1/users/me", headers=bearer(alice["tokens"]["access_token"]))
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"


async def test_refresh_token_cannot_be_used_as_access_token(client, alice):
    r = await client.get("/v1/users/me", headers=bearer(alice["tokens"]["refresh_token"]))
    assert r.status_code == 401


async def test_access_token_cannot_be_used_to_refresh(client, alice):
    r = await client.post("/v1/auth/refresh", json={"refresh_token": alice["tokens"]["access_token"]})
    assert r.status_code == 401


async def test_refresh_rotates_tokens(client, alice):
    old = alice["tokens"]["refresh_token"]
    r = await client.post("/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 200
    new = r.json()["tokens"]
    assert new["refresh_token"] != old
    me = await client.get("/v1/users/me", headers=bearer(new["access_token"]))
    assert me.status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_the_family(client, alice):
    old = alice["tokens"]["refresh_token"]
    new = (await client.post("/v1/auth/refresh", json={"refresh_token": old})).json()["tokens"]["refresh_token"]
    replay = await client.post("/v1/auth/refresh", json={"refresh_token": old})
    assert replay.status_code == 401
    # the legitimate newest token is now dead too
    after = await client.post("/v1/auth/refresh", json={"refresh_token": new})
    assert after.status_code == 401


async def test_logout_revokes_refresh_token_and_is_idempotent(client, alice):
    rt = alice["tokens"]["refresh_token"]
    assert (await client.post("/v1/auth/logout", json={"refresh_token": rt})).status_code == 204
    assert (await client.post("/v1/auth/logout", json={"refresh_token": rt})).status_code == 204
    assert (await client.post("/v1/auth/logout", json={"refresh_token": "junk"})).status_code == 204
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": rt})).status_code == 401


async def test_separate_logins_are_independent_sessions(client, alice):
    second = (await client.post("/v1/auth/login",
              json={"email": "alice@example.com", "password": ALICE["password"]})).json()["tokens"]
    await client.post("/v1/auth/logout", json={"refresh_token": alice["tokens"]["refresh_token"]})
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})).status_code == 200


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_registration_can_be_turned_off():
    from app.config import Settings
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient
    from stiamo_common.db import Base

    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:", allow_registration=False), hasher_rounds=4)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/v1/auth/register", json=ALICE)
            assert r.status_code == 403 and "turned off" in r.json()["error"]["message"]
            assert (await c.post("/v1/auth/login", json={"email": ALICE["email"], "password": ALICE["password"]})).status_code == 401
