"""Validation rules shared by the aggregates. Format checks live on the models; these are the business rules."""

import re

from stiamo_common.errors import ValidationFailed

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def required(value: str | None, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationFailed(f"{label} is required")
    return text


def blank_to_none(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def valid_email(value: str | None) -> str | None:
    email = blank_to_none(value)
    if email and not _EMAIL.match(email):
        raise ValidationFailed("Enter a valid email address")
    return email


def unique(items):
    """Order-preserving de-duplication."""
    return list(dict.fromkeys(items))
