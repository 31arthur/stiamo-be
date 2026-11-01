"""The mail service's live feed (server-sent events), read from the agent service."""

import json
from collections.abc import AsyncIterator

import httpx

from app.domain.llm import AiError


class HttpMailEvents:
    def __init__(self, http: httpx.AsyncClient, mail_url: str) -> None:
        self._http, self._url = http, mail_url.rstrip("/")

    async def __call__(self, account_id: str, folder: str, token: str) -> AsyncIterator[str]:
        """Yields "live" when the feed is up and "changed" each time the folder changes. Ends when the connection does."""
        url = f"{self._url}/v1/mail/accounts/{account_id}/events"
        # The feed is quiet for long stretches (it sends a keep-alive every 20 seconds): no read timeout.
        async with self._http.stream("GET", url, params={"folder": folder}, headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}, timeout=httpx.Timeout(None, connect=10)) as r:
            if r.status_code != 200:
                raise AiError(f"The mail service refused the feed ({r.status_code}).")
            name = ""
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    if name == "changed":
                        yield "changed"
                    elif name == "state":
                        try:
                            state = json.loads(line[5:]).get("state")
                        except ValueError:
                            state = None
                        if state == "live":
                            yield "live"
                        elif state == "auth":
                            raise AiError("The mailbox password was refused. Update it in Mail.")
                    name = ""
