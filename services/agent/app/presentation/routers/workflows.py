from typing import Annotated, Any

from fastapi import APIRouter, Response, status
from pydantic import Field
from stiamo_common.models import DomainModel

from app.presentation import deps

router = APIRouter(prefix="/v1/agent/workflows", tags=["workflows"])


class WorkflowBody(DomainModel):
    name: Annotated[str, Field(max_length=100)]
    description: Annotated[str, Field(max_length=300)] = ""
    #: The setup: source, filters, prompt, labels, decider. Validated by the server; see docs/WORKFLOW_AGENTS.md.
    config: dict[str, Any]
    #: The workspaces it is shown in, when it is built.
    workspaces: list[Annotated[str, Field(max_length=20)]] = Field(default_factory=list, max_length=10)


class TestBody(DomainModel):
    #: Pasted items for a manual source, separated by a line of ---.
    manual: Annotated[str, Field(max_length=100_000)] | None = None


class DraftTestBody(TestBody):
    config: dict[str, Any]


class SafeSendersBody(DomainModel):
    account_ids: list[Annotated[str, Field(max_length=36)]] = Field(min_length=1, max_length=10)


class ReviewBody(DomainModel):
    item_ids: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=200)
    action: str
    #: When approving: the label to use instead of the agent's (one item at a time).
    label: str | None = None


@router.get("", summary="The agents this person built")
async def list_workflows(workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return {"items": await workflows.all_for(me)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Build an agent")
async def create(body: WorkflowBody, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.create(me, body.name, body.description, body.config, body.workspaces)


@router.post("/test", summary="Try a setup that isn't saved yet on a sample (nothing is kept)")
async def test_draft(body: DraftTestBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await workflows.test_draft(me, token, body.config, body.manual)


@router.post("/prompt-preview", summary="Exactly what the model would be sent for a setup, filled with example emails (nothing is asked of any model)")
async def prompt_preview(body: DraftTestBody, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.preview(me, body.config)


@router.post("/safe-senders", summary="Who the safe-senders shield would protect: the addresses found in each mailbox's Sent folder")
async def safe_senders(body: SafeSendersBody, workflows: deps.Workflows, token: deps.Token, me: deps.Me) -> dict[str, Any]:
    return {"items": await workflows.safe_senders(token, body.account_ids)}


@router.get("/{workflow_id}", summary="One agent with its setup and ledger counts")
async def get_workflow(workflow_id: str, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.get(me, workflow_id)


@router.put("/{workflow_id}", summary="Change an agent (a changed setup is a new version)")
async def update(workflow_id: str, body: WorkflowBody, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.update(me, workflow_id, body.name, body.description, body.config)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an agent, its ledger and its history")
async def delete(workflow_id: str, workflows: deps.Workflows, me: deps.Me) -> Response:
    await workflows.delete(me, workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workflow_id}/test", summary="Test run: the saved setup on a sample, nothing applied or remembered")
async def test(workflow_id: str, body: TestBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await workflows.test(me, token, workflow_id, body.manual)


@router.post("/{workflow_id}/run", summary="Run now: decide the new items and keep each decision for review")
async def run(workflow_id: str, body: TestBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await workflows.run(me, token, workflow_id, body.manual)


@router.get("/{workflow_id}/executions", summary="Every real run of the agent, newest first")
async def executions(workflow_id: str, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.executions(me, workflow_id)


@router.get("/{workflow_id}/executions/{run_id}", summary="One run: what it read and every item it decided, as it stands now")
async def execution(workflow_id: str, run_id: str, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.execution(me, workflow_id, run_id)


@router.get("/{workflow_id}/review", summary="What the agent proposed or wasn't sure about")
async def review(workflow_id: str, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.review(me, workflow_id)


@router.post("/{workflow_id}/review", summary="Approve or dismiss items (approving may correct the label)")
async def decide_review(workflow_id: str, body: ReviewBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, int]:
    return await workflows.decide_review(me, token, workflow_id, body.item_ids, body.action, body.label)
