import bcrypt
from app.tools.import_users import NO_PASSWORD, import_users


def supabase_hash(password: str) -> str:
    # Supabase (GoTrue) stores bcrypt hashes with the $2a$ prefix
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(4, prefix=b"2a")).decode()


async def run(app, users, dry_run=False):
    async with app.state.session_factory() as session:
        report = await import_users(session, users, dry_run)
        await session.commit()
    return report


USERS = [
    {"id": "11111111-1111-1111-1111-111111111111", "email": "Ravi@Example.com", "name": "Ravi",
     "passwordHash": supabase_hash("my old password"), "createdAt": "2025-05-01T10:00:00+00:00"},
    {"id": "22222222-2222-2222-2222-222222222222", "email": "google@example.com", "name": "", "passwordHash": ""},
    {"id": "", "email": "broken@example.com"},
]


async def test_people_sign_in_with_the_password_they_already_had(app, client):
    report = await run(app, USERS)
    assert (report.imported, report.skipped, report.without_password) == (2, ["?: no id or email"], ["google@example.com"])

    login = await client.post("/v1/auth/login", json={"email": "ravi@example.com", "password": "my old password"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["id"] == "11111111-1111-1111-1111-111111111111"  # same id, so their data stays theirs
    assert login.json()["user"]["created_at"].startswith("2025-05-01")
    assert (await client.post("/v1/auth/login", json={"email": "ravi@example.com", "password": "wrong"})).status_code == 401

    token = login.json()["tokens"]["access_token"]
    me = await client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["name"] == "Ravi"


async def test_an_account_without_a_password_can_never_sign_in(app, client):
    await run(app, USERS)
    for guess in ("", NO_PASSWORD, "password123"):
        r = await client.post("/v1/auth/login", json={"email": "google@example.com", "password": guess or "x"})
        assert r.status_code == 401


async def test_import_is_repeatable_and_does_not_overwrite(app, client):
    await run(app, USERS)
    again = await run(app, [{**USERS[0], "passwordHash": supabase_hash("changed")}, *USERS[1:2]])
    assert again.imported == 0 and again.already_there == 2
    assert (await client.post("/v1/auth/login", json={"email": "ravi@example.com", "password": "my old password"})).status_code == 200


async def test_an_existing_email_with_another_id_is_left_alone(app, client):
    await client.post("/v1/auth/register", json={"name": "New", "email": "ravi@example.com", "password": "password123"})
    report = await run(app, USERS[:1])
    assert (report.imported, report.already_there) == (0, 1)


async def test_dry_run_writes_nothing(app, client):
    assert (await run(app, USERS, dry_run=True)).imported == 2
    assert (await client.post("/v1/auth/login", json={"email": "ravi@example.com", "password": "my old password"})).status_code == 401
