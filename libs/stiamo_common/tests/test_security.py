import time

import jwt
import pytest

from stiamo_common.errors import Unauthorized
from stiamo_common.security import ACCESS, REFRESH, TokenSettings, decode_token

SETTINGS = TokenSettings(verification_key="k" * 32)


def make(typ=ACCESS, **over):
    now = int(time.time())
    claims = {"sub": "u1", "jti": "j", "typ": typ, "iat": now, "exp": now + 60,
              "iss": SETTINGS.issuer, "aud": SETTINGS.audience, **over}
    return jwt.encode(claims, SETTINGS.verification_key, algorithm="HS256")


def test_valid_access_token():
    assert decode_token(make(), SETTINGS, ACCESS)["sub"] == "u1"


def test_wrong_type_rejected():
    with pytest.raises(Unauthorized):
        decode_token(make(REFRESH), SETTINGS, ACCESS)


def test_expired_rejected():
    with pytest.raises(Unauthorized):
        decode_token(make(exp=int(time.time()) - 3600), SETTINGS, ACCESS)


def test_bad_signature_rejected():
    bad = jwt.encode({"sub": "u1"}, "x" * 32, algorithm="HS256")
    with pytest.raises(Unauthorized):
        decode_token(bad, SETTINGS, ACCESS)
