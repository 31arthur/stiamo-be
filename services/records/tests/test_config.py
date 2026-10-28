import pytest
from app.config import Settings


@pytest.mark.parametrize("raw", [
    '["https://a.com","https://b.com"]',
    "https://a.com,https://b.com",
    "[https://a.com, https://b.com]",
    "https://a.com , https://b.com",
])
def test_cors_origins_formats(raw, monkeypatch):
    monkeypatch.setenv("RECORDS_CORS_ORIGINS", raw)
    assert Settings().cors_origins == ["https://a.com", "https://b.com"]
