import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.application.ports import LedgerEntry, UserSettings
from app.domain.insights import connections, mail_block, news_block, sender_of, topics, work_block
from app.domain.llm import ChatResult
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.infrastructure.db.workflows import SqlWorkflowRepository

from .conftest import headers
from .test_agent_api import choose, connect

INS = "/v1/agent/insights"
UTC_ = ZoneInfo("UTC")
TODAY = "2026-11-06"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def mail(item, status="queued", priority="normal", due=None, sender="Registrar <registrar@labour.gov.in>", title="Notice", day="2026-11-06", agent="wf-a", summary=""):
    return {"agent_id": agent, "item_id": item, "title": title, "excerpt": f"From: {sender}\nSubject: {title}", "label": "urgent", "status": status, "created_at": f"{day}T08:00:00+00:00", "data": {"priority": priority, "due": due, "summary": summary}}


def story(i, title, importance="major", source="The Hindu", day="2026-11-06"):
    return {"agent_id": "wf-n", "item_id": f"feed:{i}", "title": title, "excerpt": "", "label": "relevant", "status": "published", "created_at": f"{day}T06:00:00+00:00", "data": {"importance": importance, "weight": 3, "source": source, "link": f"https://news.example/{i}", "publishedAt": f"{day}T05:00:00Z", "relevance": 0.9}}


def test_the_sender_is_read_from_the_from_line_the_agents_keep():
    assert sender_of("From: Registrar <registrar@labour.gov.in>\nSubject: x") == ("Registrar", "registrar@labour.gov.in")
    assert sender_of("From: lead@client.io\nSubject: x") == ("", "lead@client.io") and sender_of("no sender here") == ("", "")


def test_topics_are_the_words_that_come_up_in_more_than_one_story():
    out = topics(["Workers stranded in Kuwait camp", "Kuwait amnesty for workers announced", "Gulf wages delayed for workers", "Unrelated headline entirely"])
    assert [t["term"] for t in out][:2] == ["workers", "kuwait"] and out[0]["count"] == 3 and all(t["count"] >= 2 for t in out)


def test_mail_is_counted_by_day_urgency_sender_and_what_is_due_soon():
    rows = [
        mail("a:1:1", priority="high", due="2026-11-09", title="Register by Monday"),
        mail("a:1:2", priority="normal", sender="Lead <lead@client.io>", day="2026-11-05"),
        mail("a:1:3", status="applied", sender="Shop <deals@shop.com>"),
        mail("a:1:4", status="event_proposed", priority="high", sender="Registrar <registrar@labour.gov.in>"),
        mail("a:1:5", status="kept", day="2026-10-01"),
    ]
    m = mail_block(rows, TODAY, 7, UTC_)
    assert (m["total"], m["needsYou"], m["urgent"], m["cleared"], m["events"]) == (5, 3, 2, 1, 1)
    today = next(d for d in m["perDay"] if d["date"] == TODAY)
    assert (today["urgent"], today["attention"], today["other"]) == (2, 0, 1) and len(m["perDay"]) == 7
    assert m["topSenders"][0] == {"name": "Registrar", "address": "registrar@labour.gov.in", "count": 3, "urgent": 2} and all(s["address"] != "deals@shop.com" for s in m["topSenders"])  # cleared mail is not a sender to know
    assert [d["title"] for d in m["dueSoon"]] == ["Register by Monday"] and m["topDomains"][0] == {"domain": "labour.gov.in", "count": 3}


def test_news_is_counted_by_importance_source_and_topic_and_ranked():
    rows = [story(1, "Workers stranded in Kuwait", "critical"), story(2, "Kuwait amnesty for workers", "major", "Gulf News"), story(3, "Wage theft case in Dubai", "minor", day="2026-11-03")]
    n = news_block(rows, TODAY, 7, UTC_, NOW)
    assert n["total"] == 3 and {x["level"]: x["count"] for x in n["byImportance"]} == {"critical": 1, "major": 1, "minor": 1}
    assert n["topSources"][0] == {"source": "The Hindu", "count": 2} and n["top"][0]["importance"] == "critical" and n["top"][0]["link"] == "https://news.example/1"
    assert {t["term"] for t in n["topics"]} >= {"kuwait", "workers"} and sum(d["count"] for d in n["perDay"]) == 3


def test_work_is_read_from_the_records_themselves():
    tasks = [
        {"id": "t1", "title": "Pay fee", "dueDate": "2026-11-01", "estimate": 20, "outcome": {"status": "open"}},
        {"id": "t2", "title": "Draft", "dueDate": "2026-11-08", "estimate": 90, "outcome": {"status": "open"}},
        {"id": "t3", "title": "Filed", "outcome": {"status": "done", "recordedAt": "2026-11-04T10:00:00Z"}},
    ]
    fus = [{"id": "f1", "title": "Call him", "due": {"date": "2026-11-02"}, "outcome": {"status": "open"}, "attempts": [{"date": "2026-11-03", "result": "no_answer"}, {"date": "2026-11-04", "result": "no_answer"}, {"date": "2026-11-05", "result": "reached"}]}]
    hearings = [{"id": "h1", "caseId": "c1", "date": "2026-11-10", "time": 600, "duration": 60, "purpose": "Arguments", "outcome": {"status": "open"}}]
    w = work_block(tasks, fus, [], hearings, TODAY, 7, UTC_)
    assert (w["tasks"]["open"], w["tasks"]["overdue"], w["tasks"]["dueThisWeek"], w["tasks"]["done"], w["tasks"]["doneRate"]) == (2, 1, 1, 1, 0.33) and w["tasks"]["overdueList"][0]["title"] == "Pay fee"
    assert (w["followUps"]["overdue"], w["followUps"]["tries"], w["followUps"]["reached"], w["followUps"]["reachRate"]) == (1, 3, 1, 0.33) and w["followUps"]["stuck"][0]["tries"] == 3
    load = {d["date"]: d["minutes"] for d in w["load"]}
    assert load["2026-11-08"] == 90 and load["2026-11-10"] == 60 and w["hearings"]["next"][0]["date"] == "2026-11-10" and len(w["load"]) == 7


def test_mail_and_news_are_connected_to_the_cases_they_name():
    cases = [{"id": "c1", "reference": "SA-2026-001", "title": "Alur NHAI wages", "opposingParty": "NHAI", "client": "Alur workers"}]
    hearings = [{"id": "h1", "caseId": "c1", "date": "2026-11-12", "outcome": {"status": "open"}}]
    m = [mail("a:1:1", title="Re: SA-2026-001 order", agent="wf-a")]
    n = [story(1, "NHAI told to pay wage arrears"), story(2, "Unrelated")]
    out = connections(m, n, cases, hearings, TODAY)
    assert [c["kind"] for c in out] == ["mail-case", "news-party"] and "next in court 2026-11-12" in out[0]["text"] and out[1]["link"] == "https://news.example/1"
    assert connections(m, n, [], [], TODAY) == []


async def seed(rig, rows, placed=None):
    async with rig.app.state.session_factory() as s:
        await SqlWorkflowRepository(s).record([
            LedgerEntry(f"e{i}", r["agent_id"], "u1", r["item_id"], r["title"], r["excerpt"], r["label"], 0.9, "", r["status"], "", 1, datetime.fromisoformat(r["created_at"]), None, r["data"])
            for i, r in enumerate(rows)
        ])
        if placed:
            repo = SqlSettingsRepository(s, rig.app.state.box)
            cur = await repo.get("u1") or UserSettings("u1", "owner@example.com")
            cur.workspaces = {**cur.workspaces, **placed}
            await repo.save(cur)
        await s.commit()


async def test_the_insights_put_mail_news_and_work_side_by_side_for_a_workspace_or_all(rig):
    rig.app.state.clock = lambda: NOW
    rig.data.tables = {"tasks": [{"id": "t1", "title": "SB task", "workspace": "sb", "dueDate": "2026-11-01", "estimate": 30, "outcome": {"status": "open"}}, {"id": "t2", "title": "FSA task", "workspace": "fsa", "dueDate": "2026-11-01", "estimate": 30, "outcome": {"status": "open"}}], "follow-ups": [], "meetings": [], "plans": []}
    await seed(
        rig,
        [mail("a:1:1", priority="high", agent="wf-sb"), mail("b:1:1", priority="normal", agent="wf-fsa", sender="Client <pay@fsa-client.io>"), story(1, "Workers stranded in Kuwait"), story(2, "Kuwait camp workers freed")],
        placed={"wf-fsa": ["fsa"]},
    )
    everything = (await rig.client.get(INS, params={"days": 7}, headers=headers())).json()
    assert everything["workspace"] == "all" and everything["mail"]["total"] == 2 and everything["news"]["total"] == 2 and everything["work"]["tasks"]["overdue"] == 2
    sb = (await rig.client.get(INS, params={"days": 7, "workspace": "sb"}, headers=headers())).json()
    assert sb["mail"]["total"] == 1 and sb["mail"]["urgent"] == 1 and sb["work"]["tasks"]["overdue"] == 1 and sb["work"]["tasks"]["overdueList"][0]["title"] == "SB task"
    fsa = (await rig.client.get(INS, params={"days": 7, "workspace": "fsa"}, headers=headers())).json()
    assert fsa["mail"]["total"] == 1 and fsa["mail"]["urgent"] == 0 and fsa["news"]["total"] == 0 and fsa["work"]["tasks"]["overdueList"][0]["title"] == "FSA task"  # the news desk is S&B's
    assert (await rig.client.get(INS, params={"days": 5}, headers=headers())).status_code == 422
    assert (await rig.client.get(INS, params={"workspace": "nowhere"}, headers=headers())).status_code == 422
    assert (await rig.client.get(INS)).status_code == 401


async def test_the_insights_belong_to_one_person(rig):
    rig.app.state.clock = lambda: NOW
    await seed(rig, [mail("a:1:1", priority="high")])
    other = (await rig.client.get(INS, params={"days": 7}, headers=headers("b@example.com", "u2"))).json()
    assert other["mail"]["total"] == 0


async def test_a_written_digest_is_made_from_the_figures_and_kept_for_the_day(rig):
    rig.app.state.clock = lambda: NOW
    await seed(rig, [mail("a:1:1", priority="high", due="2026-11-09", title="Register by Monday")])
    no_model = await rig.client.post(f"{INS}/digest", json={"workspace": "all", "days": 7}, headers=headers())
    assert no_model.status_code == 422 and "Choose a provider" in no_model.json()["error"]["message"]
    await connect(rig)
    await choose(rig, agent="/v1/agent/agents/cases")
    rig.script += [ChatResult(json.dumps({"summary": "One urgent email needs you by Monday.", "highlights": ["Register by Monday (due 2026-11-09)", ""], "suggestions": ["Reply to the registrar today"]}))]
    first = (await rig.client.post(f"{INS}/digest", json={"workspace": "all", "days": 7}, headers=headers())).json()
    assert first["summary"].startswith("One urgent email") and first["highlights"] == ["Register by Monday (due 2026-11-09)"] and first["suggestions"] == ["Reply to the registrar today"] and first["cached"] is False
    shown = rig.llm_log[-1]["last"]
    assert "Register by Monday" in shown and "needsYou" in shown and "excerpt" not in shown  # the figures and the few items, not every row
    asked = len(rig.llm_log)
    again = (await rig.client.post(f"{INS}/digest", json={"workspace": "all", "days": 7}, headers=headers())).json()
    assert again["cached"] is True and again["summary"] == first["summary"] and len(rig.llm_log) == asked
    rig.script += [ChatResult("not json")]
    bad = await rig.client.post(f"{INS}/digest", json={"workspace": "all", "days": 7, "force": True}, headers=headers())
    assert bad.status_code == 422 and "couldn't be read" in bad.json()["error"]["message"]
