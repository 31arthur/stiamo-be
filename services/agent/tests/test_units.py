import httpx
import pytest
from app.application.runner import Tool, ToolOutput, run_agent
from app.application.tools import case_search_terms, drive_file_id, match_cases
from app.domain.llm import AiError, ChatResult, Message, ToolCall, ToolSpec, clamp_thinking
from app.domain.prompt import PromptConfig, ToolOverride, build_package, render
from app.infrastructure.llm.gemini import gemini_models, thinking_config, to_contents
from app.infrastructure.llm.jev import JevClient, check_questions
from app.infrastructure.llm.openai_compat import openai_models, openrouter_models, to_wire
from app.infrastructure.messaging import as_whatsapp, twilio_signature, valid_twilio_signature
from app.infrastructure.security import SecretBox
from cryptography.fernet import Fernet
from stiamo_common.errors import ValidationFailed


def test_variables_are_filled_in_and_values_are_never_read_for_more():
    assert render("Hi {{ user }}, {{input}} {{unknown}}!", {"user": "Arj", "input": "{{user}}"}) == "Hi Arj, {{user}} !"


def test_a_prompt_must_use_known_variables_and_keep_the_input():
    with pytest.raises(ValidationFailed, match="unknown variables"):
        PromptConfig(system="{{wat}}").validated()
    with pytest.raises(ValidationFailed, match="must contain"):
        PromptConfig(input_template="ask").validated()
    with pytest.raises(ValidationFailed, match="empty"):
        PromptConfig(system="  ").validated()
    PromptConfig().validated()


def test_the_package_applies_tool_overrides():
    tools = [ToolSpec("a", "does a", {}), ToolSpec("b", "does b", {})]
    cfg = PromptConfig(system="{{tools}}", tools={"a": ToolOverride(description="renamed"), "b": ToolOverride(enabled=False)})
    pkg = build_package(cfg, tools, {}, "q")
    assert [t.name for t in pkg.tools] == ["a"] and pkg.system == "- a: renamed" and pkg.user == "q"


def test_thinking_is_replaced_by_a_level_the_model_takes():
    assert clamp_thinking((), "high") == "off"
    assert clamp_thinking(("low", "medium", "high"), "high") == "high"
    assert clamp_thinking(("low", "high"), "medium") == "low"
    assert clamp_thinking(("off", "low", "medium"), "weird") == "medium"


def test_decision_questions_are_checked_before_they_are_sent():
    ok = {"q": {"type": "choice", "instructions": "?", "criteria": {"a": "A", "b": "B"}}, "n": {"type": "noul", "instructions": "?", "criteria": {"true": "t", "false": "f"}}, "s": {"type": "score", "instructions": "?", "criteria": ["low", "high"]}}
    assert check_questions(ok) is ok
    for bad, why in [({}, "at least one"), ({"q": {"type": "x"}}, "needs a type"), ({"q": {"type": "choice", "instructions": "?", "criteria": {"a": "A"}}}, "two options"), ({"q": {"type": "noul", "instructions": "?", "criteria": {"true": "t"}}}, "true"), ({"q": {"type": "score", "instructions": "?", "criteria": ["one"]}}, "list")]:
        with pytest.raises(AiError, match=why):
            check_questions(bad)


async def test_jev_posts_to_the_decisions_endpoint_and_returns_the_answers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["Authorization"], request.read().decode()
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.9}}, "model": "typesafe/jev-1.14", "usage": {"cost": 0.1}})

    client = JevClient("k", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await client.decide(model="typesafe/jev-1.14", state="x", questions={"q": {"type": "noul", "instructions": "?", "criteria": {"true": "t", "false": "f"}}})
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions" and seen["auth"] == "Bearer k" and '"model":"typesafe/jev-1.14"' in seen["body"].replace(" ", "")
    assert out["answers"]["q"]["noul"] == 0.9 and out["cost"] == 0.1


async def test_jev_explains_failures():
    client = JevClient("k", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(402, json={"error": {"message": "x"}}))))
    with pytest.raises(AiError, match="credit"):
        await client.decide(model="m", state="s", questions={"q": {"type": "noul", "instructions": "?", "criteria": {"true": "t", "false": "f"}}})


def test_model_lists_keep_only_what_can_run_an_agent():
    assert sorted(m.id for m in openai_models({"data": [{"id": "gpt-4o"}, {"id": "o3"}, {"id": "whisper-1"}, {"id": "text-embedding-3"}, {"id": "o1-mini"}]})) == ["gpt-4o", "o3"]
    out = openrouter_models({"data": [{"id": "a", "name": "A", "supported_parameters": ["tools", "reasoning"]}, {"id": "b", "supported_parameters": ["temperature"]}]})
    assert [(m.id, m.thinking) for m in out] == [("a", ("off", "low", "medium", "high"))]
    g = {m.id: m.thinking for m in gemini_models([{"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]}, {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]}, {"name": "models/gemini-embedding", "supportedGenerationMethods": ["embedContent"]}])}
    assert g == {"gemini-2.5-pro": ("low", "medium", "high"), "gemini-3-pro": ("low", "high")}
    assert thinking_config("gemini-2.5-pro", "off") is None and thinking_config("gemini-2.5-flash", "off") == {"thinkingBudget": 0}


def test_conversations_are_written_the_way_each_api_wants():
    msgs = [Message("assistant", "", [ToolCall("c1", "x", {"a": 1})]), Message("tool", '{"ok":true}', tool_call_id="c1", name="x")]
    wire = to_wire(msgs)
    assert wire[0]["tool_calls"][0]["function"] == {"name": "x", "arguments": '{"a": 1}'} and wire[1] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}
    raw = {"role": "model", "parts": [{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": "s"}]}
    contents = to_contents([Message("user", "q"), Message("assistant", "", [ToolCall("0", "a", {})], raw=raw), Message("tool", '{"n":1}', name="a"), Message("tool", '{"n":2}', name="b")])
    assert contents[1]["parts"] is raw["parts"] and len(contents) == 3 and len(contents[2]["parts"]) == 2


async def test_the_agent_loop_runs_tools_survives_a_failing_one_and_stops_at_the_limit():
    class Loop:
        def __init__(self):
            self.n = 0

        async def chat(self, *, model, messages, tools, thinking):
            self.n += 1
            return ChatResult("Enough.") if not tools else ChatResult("", [ToolCall(str(self.n), "echo", {"w": "x"}), ToolCall("z", "nope", {})])

    async def echo(a):
        return ToolOutput({"said": a["w"]}, [{"kind": "case", "id": "1"}])

    tool = Tool(ToolSpec("echo", "e", {}), lambda a: "Echoed", echo)
    run = await run_agent(client=Loop(), model="m", thinking="off", system="s", user="q", tools=[tool], max_steps=2)
    assert run.answer == "Enough." and [s.error for s in run.steps][1] == "There is no tool called nope." and len(run.found) == 1


def test_cases_are_found_by_what_a_person_would_type():
    cases = [{"id": "1", "reference": "SA-1", "title": "Alur NHAI wages", "opposingParty": "NHAI", "courtNumber": "WP 1290/2026"}, {"id": "2", "reference": "SA-2", "title": "Brick kiln"}]
    assert [c["id"] for c in match_cases(cases, "nhai alur")] == ["1"] and [c["id"] for c in match_cases(cases, "wp 1290")] == ["1"]
    assert case_search_terms({"reference": "SA-1", "title": "  Wages   case ", "client": "wages case", "opposingParty": "AB"}) == ["SA-1", "Wages case"]
    assert drive_file_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view") == "1AbCdEfGhIjKlMnOpQrStUv"


def test_secrets_are_sealed_and_a_wrong_key_reads_as_empty():
    a, b = SecretBox(Fernet.generate_key()), SecretBox(Fernet.generate_key())
    sealed = a.seal({"openai": "sk-secret"})
    assert "sk-secret" not in sealed and a.open(sealed) == {"openai": "sk-secret"} and b.open(sealed) == {}


def test_twilio_signatures_and_numbers():
    url, params = "https://x.example/hook", {"Body": "hi", "From": "whatsapp:+1"}
    sig = twilio_signature("token", url, params)
    assert valid_twilio_signature("token", url, params, sig) and not valid_twilio_signature("other", url, params, sig) and not valid_twilio_signature("token", url, params, None)
    assert as_whatsapp("+91 98765-43210") == "whatsapp:+919876543210" == as_whatsapp("whatsapp:+919876543210")
