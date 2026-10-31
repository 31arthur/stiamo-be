"""The decisions pack: asking a decision model (Jev) typed questions and getting probabilities back. Empty until it is switched on."""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.domain.llm import AiError, ToolSpec

GUIDE = (
    "Ask a decision model typed questions about some text, and get probabilities back instead of prose. Use it when you must pick between options "
    "(choice), judge whether something holds (noul, the probability of yes) or place something on an ordered scale (score). "
    "Put everything the question needs in `state`. Give every question `type`, `instructions` and `criteria`: for choice an object of option -> description, "
    "for noul an object with `true` and `false` descriptions, for score an array of descriptions from lowest to highest."
)


class DecisionsPack(ToolPack):
    name = "decisions"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        decisions, model = ctx.decisions, ctx.decision_model
        if decisions is None or not model:
            return []

        async def decide(a: dict[str, Any]) -> ToolOutput:
            questions = a.get("questions")
            if not isinstance(questions, dict) or not questions:
                return ToolOutput({"error": "Give at least one question."})
            try:
                answers = await decisions.decide(model=model, state=a.get("state", ""), questions=questions)
            except AiError as exc:  # told to the model, which can fix its questions, rather than ending the whole run
                return ToolOutput({"error": str(exc)})
            return ToolOutput({"answers": answers})

        return [
            Tool(
                ToolSpec(
                    "decide", GUIDE,
                    {
                        "type": "object",
                        "properties": {
                            "state": {"type": "string", "description": "The text to decide about, with everything needed."},
                            "questions": {"type": "object", "description": "Question name -> {type, instructions, criteria}."},
                        },
                        "required": ["state", "questions"],
                    },
                ),
                lambda a: f"Asked the decision model ({', '.join(map(str, (a.get('questions') or {}).keys())) or 'no questions'})",
                decide,
            )
        ]
