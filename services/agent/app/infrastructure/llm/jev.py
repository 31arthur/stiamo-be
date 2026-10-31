"""Jev: a decision model from TypeSafe, reached through OpenRouter. It answers typed questions with probabilities, not text."""

from typing import Any

import httpx

from app.application.ports import DecisionClient
from app.domain.llm import AiError

URL = "https://openrouter.ai/api/alpha/decisions"
KINDS = ("choice", "noul", "score")


def check_questions(questions: dict[str, Any]) -> dict[str, Any]:
    """Catches the mistakes the API would answer with a 400, so they can be explained in words."""
    if not questions:
        raise AiError("Ask at least one question.")
    for name, q in questions.items():
        if not isinstance(q, dict) or q.get("type") not in KINDS:
            raise AiError(f"Question “{name}” needs a type: choice, noul or score.")
        if not q.get("instructions"):
            raise AiError(f"Question “{name}” needs instructions (what is being decided).")
        c = q.get("criteria")
        if q["type"] == "choice" and not (isinstance(c, dict) and len(c) >= 2):
            raise AiError(f"Question “{name}” is a choice, so its criteria need at least two options (option → description).")
        if q["type"] == "noul" and not (isinstance(c, dict) and "true" in c and "false" in c):
            raise AiError(f"Question “{name}” is a yes/no, so its criteria need both a “true” and a “false” description.")
        if q["type"] == "score" and not (isinstance(c, list) and len(c) >= 2):
            raise AiError(f"Question “{name}” is a score, so its criteria need a list of at least two levels, lowest first.")
    return questions


class JevClient(DecisionClient):
    def __init__(self, api_key: str, http: httpx.AsyncClient) -> None:
        self._key, self._http = api_key, http

    async def decide(self, *, model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        body = {"model": model, "state": state, "questions": check_questions(questions)}
        try:
            r = await self._http.post(URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as exc:
            raise AiError("Couldn't reach OpenRouter. Try again in a moment.") from exc
        if r.status_code != 200:
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message", "")
            except ValueError:
                pass
            if r.status_code in (401, 403):
                raise AiError("OpenRouter didn't accept the key. Check it on the Providers page.", r.status_code)
            if r.status_code == 402:
                raise AiError("OpenRouter says the account is out of credit.", r.status_code)
            raise AiError(f"The decision model couldn't answer ({r.status_code}). {detail}".strip(), r.status_code)
        out = r.json()
        return {"answers": out.get("answers", {}), "model": out.get("model"), "cost": (out.get("usage") or {}).get("cost")}
