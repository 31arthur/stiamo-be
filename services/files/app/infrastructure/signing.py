"""Short-lived download links the browser can use directly (<img src>, <iframe src>) without an Authorization header."""

import time

import jwt
from stiamo_common.errors import Unauthorized


def sign(secret: str, user_id: str, attachment_id: str, ttl: int) -> str:
    now = int(time.time())
    claims = {"typ": "file", "usr": user_id, "att": attachment_id, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, secret, "HS256")


def verify(secret: str, token: str) -> tuple[str, str]:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "usr", "att"]})
    except jwt.PyJWTError as exc:
        raise Unauthorized("This download link has expired or is not valid.") from exc
    if claims.get("typ") != "file":
        raise Unauthorized("This download link has expired or is not valid.")
    return claims["usr"], claims["att"]
