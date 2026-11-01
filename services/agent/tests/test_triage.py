import json

from app.domain.llm import ChatResult

from .conftest import headers
from .test_agent_api import choose
from .test_mail_filter import BASE, answer, make

EVENT = {"title": "Kickoff call", "date": "2026-11-12", "start": "10:00", "end": "11:30", "mode": "virtual", "link": "https://meet.example.com/x", "attendees": ["Lead"]}


def analysis(event=None, summary="The lead asks for a quote and proposes a call.", priority="high"):
    return ChatResult(json.dumps({"summary": summary, "priority": priority, "action": "Reply with a quote", "due": "2026-11-10", "event": event}), usage={"inputTokens": 80, "outputTokens": 30, "cost": 0.0005})


async def make_triage(rig, upstream, workspaces=("sb",), **over):
    cfg = {
        "source": {"kind": "agent", "upstreamId": upstream},
        "filters": {"sinceDays": None, "newOnly": True, "limit": 50, "contains": "Partnership"},
        "prompt": "Find what needs the owner in the S&B inbox.",
        "labels": [
            {"name": "meeting", "description": "An invitation to a meeting, call, conference or event.", "route": "event"},
            {"name": "issue", "description": "A problem or an important message that needs action.", "route": "attention"},
            {"name": "fyi", "description": "Informational: nothing to do.", "route": "none"},
        ],
        "decider": "model", "defaultThreshold": 0.7,
    }
    cfg.update(over)
    r = await rig.client.post(BASE, json={"name": "S&B triage", "description": "", "config": cfg, "workspaces": list(workspaces)}, headers=headers())
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    await choose(rig, agent=f"/v1/agent/agents/{wid}")
    return wid


async def queue(rig, workspace="sb", who=None):
    return (await rig.client.get(f"/v1/agent/attention?workspace={workspace}", headers=who or headers())).json()


async def test_the_classifier_goes_first_then_an_event_is_proposed_and_approved_into_the_calendar(rig):
    rig.data.bodies[4] = "Hello, shall we have a kickoff call on 12 November at 10:00? Link: https://meet.example.com/x"
    cls = await make(rig)
    tri = await make_triage(rig, cls)
    rig.script += [answer("keep"), answer("spam"), answer("meeting", 0.9), analysis(EVENT)]
    run = (await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())).json()
    assert rig.data.trashed == [3]  # the classifier ran first and cleared the spam
    assert run["counts"]["events"] == 1 and run["counts"]["items"] == 1 and "events to approve" in run["summary"]
    assert "kickoff call on 12 November" in rig.llm_log[-1]["last"]  # it read the whole email, not just the start

    q = await queue(rig)
    assert q["count"] == 1
    item = q["items"][0]
    assert (item["status"], item["label"], item["priority"], item["agentName"]) == ("event_proposed", "meeting", "high", "S&B triage")
    assert item["summary"].startswith("The lead asks") and item["event"]["date"] == "2026-11-12" and item["action"] == "Reply with a quote" and item["due"] == "2026-11-10"

    edited = {**item["event"], "title": "Kickoff call with Acme", "start": "10:30"}
    done = await rig.client.post("/v1/agent/attention/act", json={"agentId": tri, "itemId": item["itemId"], "action": "approve_event", "event": edited}, headers=headers())
    assert done.status_code == 200 and done.json()["status"] == "approved"
    m = rig.data.meetings[0]
    assert (m["title"], m["workspace"], m["mode"], m["link"]) == ("Kickoff call with Acme", "sb", "virtual", "https://meet.example.com/x")
    assert m["slot"] == {"date": "2026-11-12", "start": 630, "duration": 60}  # 10:30 to the 11:30 end the email gave
    assert "Partnership opportunity" in m["purpose"] and m["id"]
    assert (await queue(rig))["count"] == 0
    again = await rig.client.post("/v1/agent/attention/act", json={"agentId": tri, "itemId": item["itemId"], "action": "approve_event"}, headers=headers())
    assert again.status_code == 409 and len(rig.data.meetings) == 1


async def test_an_issue_waits_in_the_queue_until_it_is_done(rig):
    cls = await make(rig)
    tri = await make_triage(rig, cls)
    rig.script += [answer("keep"), answer("spam"), answer("issue", 0.8), analysis(None, "A client reports the wages are still unpaid.")]
    run = (await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())).json()
    assert run["counts"]["queued"] == 1 and run["counts"].get("events", 0) == 0
    item = (await queue(rig))["items"][0]
    assert item["status"] == "queued" and item["event"] is None and "wages" in item["summary"]
    assert (await rig.client.post("/v1/agent/attention/act", json={"agentId": tri, "itemId": item["itemId"], "action": "done"}, headers=headers())).json() == {"status": "done"}
    assert (await queue(rig))["count"] == 0
    detail = (await rig.client.get(f"{BASE}/{tri}/executions/{run['runId']}", headers=headers())).json()
    assert detail["items"][0]["status"] == "done"  # shown as it stands now


async def test_an_event_without_a_real_date_is_just_an_item_to_look_at(rig):
    cls = await make(rig)
    tri = await make_triage(rig, cls)
    rig.script += [answer("keep"), answer("spam"), answer("meeting"), analysis({**EVENT, "date": "next Tuesday"})]
    await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())
    item = (await queue(rig))["items"][0]
    assert item["status"] == "queued" and item["event"] is None


async def test_an_informational_email_never_reaches_the_queue(rig):
    cls = await make(rig)
    tri = await make_triage(rig, cls)
    rig.script += [answer("keep"), answer("spam"), answer("fyi")]
    out = (await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())).json()
    assert out["counts"]["kept"] == 1 and out["counts"].get("queued", 0) == 0 and (await queue(rig))["count"] == 0


async def test_a_test_run_does_not_run_the_classifier_or_move_anything(rig):
    cls = await make(rig)
    tri = await make_triage(rig, cls)
    out = (await rig.client.post(f"{BASE}/{tri}/test", json={}, headers=headers())).json()
    assert out["counts"]["items"] == 0 and rig.data.trashed == [] and rig.llm_log == []


async def test_a_missing_upstream_agent_is_explained(rig):
    await make(rig)
    tri = await make_triage(rig, "wf-gone")
    r = await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())
    assert r.status_code == 422 and "doesn't exist any more" in r.json()["error"]["message"]
    bad = await rig.client.post(BASE, json={"name": "x", "description": "", "config": {"source": {"kind": "agent"}, "prompt": "p", "labels": [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]}}, headers=headers())
    assert bad.status_code == 422


async def test_the_queue_is_per_workspace_and_private(rig):
    cls = await make(rig)
    tri = await make_triage(rig, cls, workspaces=("sb",))
    rig.script += [answer("keep"), answer("spam"), answer("issue", 0.8), analysis(None)]
    await rig.client.post(f"{BASE}/{tri}/run", json={}, headers=headers())
    assert (await queue(rig, "sb"))["count"] == 1 and (await queue(rig, "fsa"))["count"] == 0
    assert (await rig.client.get("/v1/agent/attention", headers=headers())).json()["count"] == 1
    other = headers("b@example.com", "u2")
    assert (await queue(rig, "sb", other))["count"] == 0
    item = (await queue(rig))["items"][0]
    assert (await rig.client.post("/v1/agent/attention/act", json={"agentId": tri, "itemId": item["itemId"], "action": "done"}, headers=other)).status_code == 404
    assert (await rig.client.post("/v1/agent/attention/act", json={"agentId": tri, "itemId": item["itemId"], "action": "explode"}, headers=headers())).status_code == 422
    assert (await rig.client.get("/v1/agent/attention")).status_code == 401
