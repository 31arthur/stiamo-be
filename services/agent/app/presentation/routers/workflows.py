from typing import Annotated, Any

from fastapi import APIRouter, Request, Response, status
from pydantic import Field
from stiamo_common.errors import ValidationFailed
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
    #: Why, when the person says an email is not spam. Kept with the decision.
    note: Annotated[str, Field(max_length=500)] | None = None


class FeedbackBody(DomainModel):
    item_id: Annotated[str, Field(max_length=200)]
    reason: Annotated[str, Field(max_length=500)]


class RefinementBody(DomainModel):
    point: Annotated[str, Field(max_length=400)] | None = None
    protect_domain: Annotated[str, Field(max_length=253)] | None = None


@router.get("", summary="The agents this person built")
async def list_workflows(workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return {"items": await workflows.all_for(me)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Build an agent")
async def create(body: WorkflowBody, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    made = await workflows.create(me, body.name, body.description, body.config, body.workspaces)
    await _listen(request, workflows, me, made)
    return made


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
async def update(workflow_id: str, body: WorkflowBody, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    changed = await workflows.update(me, workflow_id, body.name, body.description, body.config)
    await _listen(request, workflows, me, changed)
    return changed


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an agent, its ledger and its history")
async def delete(workflow_id: str, request: Request, workflows: deps.Workflows, me: deps.Me) -> Response:
    await workflows.delete(me, workflow_id)
    request.app.state.listeners.stop(workflow_id)
    request.app.state.schedules.stop(workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workflow_id}/rebuild", status_code=status.HTTP_201_CREATED, summary="Make a copy of this mail-sorting agent as a workflow on the canvas (this one is left as it is, so the two can run side by side)")
async def rebuild(workflow_id: str, workflows: deps.Workflows, agent: deps.Agent, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await agent.rebuild_on_canvas(me, token, await workflows.get(me, workflow_id))


@router.post("/{workflow_id}/test", summary="Test run: the saved setup on a sample, nothing applied or remembered")
async def test(workflow_id: str, body: TestBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await workflows.test(me, token, workflow_id, body.manual)


@router.post("/{workflow_id}/run", summary="Run now: decide the new items and keep each decision for review")
async def run(workflow_id: str, body: TestBody, workflows: deps.Workflows, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await workflows.run(me, token, workflow_id, body.manual)


@router.post("/{workflow_id}/feedback", summary="\"This wasn't spam, because…\": suggests one general point to add to the prompt (nothing is changed yet)")
async def feedback(workflow_id: str, body: FeedbackBody, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.analyse_feedback(me, workflow_id, body.item_id, body.reason)


@router.post("/{workflow_id}/refinements", summary="Add a point the agent learned, and/or a domain it always keeps")
async def add_refinement(workflow_id: str, body: RefinementBody, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.add_refinement(me, workflow_id, body.point, body.protect_domain)


@router.delete("/{workflow_id}/refinements/{index}", summary="Forget one learned point")
async def remove_refinement(workflow_id: str, index: int, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    return await workflows.remove_refinement(me, workflow_id, index)


@router.post("/{workflow_id}/sweep", status_code=status.HTTP_202_ACCEPTED, summary="Run through the whole inbox in the background: again and again, a batch at a time, until done")
async def start_sweep(workflow_id: str, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    w = await workflows.get(me, workflow_id)
    if w["config"].get("source", {}).get("kind") not in ("mail", "table"):
        raise ValidationFailed("Only an agent that reads a mailbox or a collection can run through everything.")
    return {"sweep": request.app.state.sweeps.start(workflow_id, me.user_id, deps.sweep_run(request.app, me, workflow_id)).json()}


@router.get("/{workflow_id}/sweep", summary="How the background run is going (or how the last one ended)")
async def sweep_status(workflow_id: str, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    await workflows.get(me, workflow_id)
    s = request.app.state.sweeps.get(workflow_id)
    return {"sweep": s.json() if s and s.user_id == me.user_id else None}


@router.post("/{workflow_id}/sweep/stop", summary="Stop after the run in progress")
async def stop_sweep(workflow_id: str, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    await workflows.get(me, workflow_id)
    s = request.app.state.sweeps.stop(workflow_id)
    return {"sweep": s.json() if s and s.user_id == me.user_id else None}


@router.get("/{workflow_id}/listen", summary="Whether the agent is listening to its mailbox, and how that is going")
async def listen_status(workflow_id: str, request: Request, workflows: deps.Workflows, me: deps.Me) -> dict[str, Any]:
    await workflows.get(me, workflow_id)
    return {**request.app.state.listeners.status(workflow_id), "schedule": request.app.state.schedules.status(workflow_id)}


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
    return await workflows.decide_review(me, token, workflow_id, body.item_ids, body.action, body.label, body.note)


async def _listen(request: Request, workflows: deps.Workflows, me: deps.Me, workflow: dict[str, Any]) -> None:
    """Listening follows the setup: switched on, changed or off, it is started, replaced or stopped."""

    async def upstream(wid: str):
        try:
            return (await workflows.get(me, wid))["config"]
        except Exception:  # noqa: BLE001  the agent it goes on from is gone: there is nothing to listen to
            return None

    await deps.sync_listener(request.app, me, workflow["id"], workflow["config"], upstream)
