import pytest
from app.application.throttle import LoginThrottle, TooManyAttempts

from .conftest import ALICE

LOGIN = {"email": ALICE["email"], "password": "wrong password"}


async def test_pauses_sign_in_after_too_many_wrong_passwords(client, alice):
    for _ in range(5):
        assert (await client.post("/v1/auth/login", json=LOGIN)).status_code == 401
    r = await client.post("/v1/auth/login", json=LOGIN)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    # Even the right password has to wait.
    assert (await client.post("/v1/auth/login", json={"email": ALICE["email"], "password": ALICE["password"]})).status_code == 429


async def test_a_right_password_clears_the_count(client, alice):
    for _ in range(4):
        await client.post("/v1/auth/login", json=LOGIN)
    assert (await client.post("/v1/auth/login", json={"email": ALICE["email"], "password": ALICE["password"]})).status_code == 200
    for _ in range(4):
        assert (await client.post("/v1/auth/login", json=LOGIN)).status_code == 401


async def test_other_people_are_not_affected(client, alice):
    for _ in range(6):
        await client.post("/v1/auth/login", json=LOGIN)
    assert (await client.post("/v1/auth/login", json={"email": "bob@example.com", "password": "nope nope"})).status_code == 401


def test_the_pause_ends():
    clock = [0.0]
    t = LoginThrottle(2, 60, now=lambda: clock[0])
    t.failed("k"), t.failed("k")
    with pytest.raises(TooManyAttempts):
        t.check("k")
    clock[0] = 61
    t.check("k")
