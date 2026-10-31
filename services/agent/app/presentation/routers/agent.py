from typing import Annotated, Any

from fastapi import APIRouter, Response, status
from pydantic import Field
from stiamo_common.models import DomainModel

from app.domain.llm import ModelInfo
from app.domain.prompt import PromptConfig, ToolOverride
from app.presentation import deps

router = APIRouter(prefix="/v1/agent", tags=["agent"])


def model_json(m: ModelInfo) -> dict[str, Any]:
    return {"id": m.id, "name": m.name, "thinking": list(m.thinking)}


def run_json(run) -> dict[str, Any]:
    return {"answer": run.answer, "steps": [s.to_json() for s in run.steps], "found": run.found, "messages": [m.to_json() for m in run.messages]}


class KeyBody(DomainModel):
    api_key: Annotated[str, Field(max_length=500)]


class SelectionBody(DomainModel):
    provider_id: str
    model_id: Annotated[str, Field(max_length=200)]
    thinking: str = "off"


class DecisionBody(DomainModel):
    enabled: bool
    model_id: str


class PromptBody(DomainModel):
    system: Annotated[str, Field(max_length=20000)]
    input_template: Annotated[str, Field(max_length=20000)]
    tools: dict[str, ToolOverride] = Field(default_factory=dict)

    def config(self) -> PromptConfig:
        return PromptConfig(system=self.system, input_template=self.input_template, tools=self.tools)


class PreviewBody(DomainModel):
    question: Annotated[str, Field(max_length=4000)] = ""
    #: Judge a prompt before saving it; without this, the saved prompt is shown.
    prompt: PromptBody | None = None


class ChatBody(DomainModel):
    message: Annotated[str, Field(max_length=4000)]
    #: What came back from the last turn, to carry the conversation on.
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=80)


class DecideBody(DomainModel):
    state: Any
    questions: dict[str, Any]
    model_id: str | None = None


# --- shared by every agent: the providers and the decision model --------------------------------------------


@router.get("/settings", summary="The providers, the decision model, and a line on each agent")
async def get_settings(agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    return await agent.view(me)


@router.put("/providers/{provider}", summary="Check a key with its provider and keep it (sealed)")
async def put_key(provider: str, body: KeyBody, agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    models = await agent.put_key(me, provider, body.api_key)
    return {"connected": True, "last4": body.api_key.strip()[-4:], "models": [model_json(m) for m in models]}


@router.delete("/providers/{provider}", status_code=status.HTTP_204_NO_CONTENT, summary="Forget a key")
async def delete_key(provider: str, agent: deps.Agent, me: deps.Me) -> Response:
    await agent.remove_key(me, provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/providers/{provider}/models", summary="The models the provider offers this key, live")
async def models(provider: str, agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    return {"items": [model_json(m) for m in await agent.models(me, provider)]}


@router.put("/decision", status_code=status.HTTP_204_NO_CONTENT, summary="Turn the decision model (Jev) on or off for the agents")
async def set_decision(body: DecisionBody, agent: deps.Agent, me: deps.Me) -> Response:
    await agent.set_decision(me, body.enabled, body.model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/decide", summary="Ask the decision model (Jev) typed questions about some text")
async def decide(body: DecideBody, agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    return await agent.decide(me, body.state, body.questions, body.model_id)


# --- one agent: its model, its prompt, and asking it ------------------------------------------------------------


@router.get("/agents/{agent_id}", summary="One agent: its prompt, the defaults, the variables it offers and the model it uses")
async def get_agent(agent_id: str, agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    return await agent.agent(me, agent_id)


@router.put("/agents/{agent_id}/selection", status_code=status.HTTP_204_NO_CONTENT, summary="Choose the provider, model and thinking level for an agent")
async def set_selection(agent_id: str, body: SelectionBody, agent: deps.Agent, me: deps.Me) -> Response:
    await agent.set_selection(me, agent_id, body.provider_id, body.model_id, body.thinking)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/agents/{agent_id}/prompt", status_code=status.HTTP_204_NO_CONTENT, summary="Save an agent's prompt, input wrapper and tool overrides")
async def set_prompt(agent_id: str, body: PromptBody, agent: deps.Agent, me: deps.Me) -> Response:
    await agent.set_prompt(me, agent_id, body.config())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/agents/{agent_id}/prompt", status_code=status.HTTP_204_NO_CONTENT, summary="Back to the agent's default prompt")
async def reset_prompt(agent_id: str, agent: deps.Agent, me: deps.Me) -> Response:
    await agent.reset_prompt(me, agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agents/{agent_id}/prompt/preview", summary="Exactly what would be sent to the model for a question")
async def preview(agent_id: str, body: PreviewBody, agent: deps.Agent, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await agent.preview(me, token, agent_id, body.question, body.prompt.config() if body.prompt else None)


@router.post("/agents/{agent_id}/chat", summary="Ask an agent")
async def chat(agent_id: str, body: ChatBody, agent: deps.Agent, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return run_json(await agent.chat(me, token, agent_id, body.message, body.messages))
