"""Running AI jobs (see domain/aijobs.py): each with its own model choice, checked by code, asked once more when the answer can't be
used, and falling back to a safe answer after that. Never raises for a model's mistake: a job that fails gives its fallback and says so."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.application.ports import LlmClient
from app.domain.aijobs import Job, JobResult, Problem, as_messages, parse_json
from app.domain.llm import AiError, Message

log = logging.getLogger(__name__)

#: One retry: enough for a model that wrapped its JSON in prose or paraphrased a quote; more only spends money on a model that can't do it.
MAX_ATTEMPTS = 2


def job_key(job: Job | str) -> str:
    """Where a job's own provider and model are kept among the agents' selections. Without one it uses the model chosen last."""
    return f"job:{job if isinstance(job, str) else job.name}"


class JobRunner:
    def __init__(self, client: LlmClient, model: str, thinking: str = "off") -> None:
        self._client, self._model, self._thinking = client, model, thinking

    @property
    def model(self) -> str:
        return self._model

    async def run(self, job: Job, inputs: dict[str, Any]) -> JobResult:
        messages = [Message(role, text) for role, text in as_messages(job, inputs)]  # type: ignore[arg-type]
        problems: list[str] = []
        replies: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                reply = await self._client.chat(model=self._model, messages=messages, tools=[], thinking=self._thinking)
            except AiError as exc:
                problems.append(f"the model couldn't be reached: {exc}")
                log.info("job %s: provider error on attempt %d", job.name, attempt)
                break
            replies.append(reply.text)
            try:
                checked = job.check(parse_json(reply.text), inputs)
            except Problem as exc:
                problems.append(str(exc))
                messages += [Message("assistant", reply.text), Message("user", f"That answer can't be used: {exc} Reply again with the JSON object only.")]
                continue
            return JobResult(checked.output, True, attempt, checked.dropped, problems, self._model, replies)
        log.info("job %s fell back after %d attempt(s): %s", job.name, len(replies), "; ".join(problems))
        return JobResult(job.fallback(inputs), False, len(replies), [], problems, self._model, replies)


def runner_for(selection: dict[str, Any] | None, keys: dict[str, str], llm_factory: Callable[[str, str], LlmClient]) -> JobRunner | None:
    """A runner on the chosen provider and model, or None when nothing usable is chosen (the caller then does without AI)."""
    if not selection or not selection.get("modelId") or selection.get("providerId") not in keys:
        return None
    return JobRunner(llm_factory(selection["providerId"], keys[selection["providerId"]]), selection["modelId"], selection.get("thinking", "off"))
