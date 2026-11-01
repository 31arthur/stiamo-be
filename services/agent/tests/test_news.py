import asyncio
from datetime import UTC, datetime, timedelta

from app.application.schedules import ScheduleManager
from app.domain.workflows import rank_news

from .conftest import headers
from .test_agent_api import choose, connect
from .test_listen import until
from .test_mail_filter import BASE, answer

NEWS = "/v1/agent/news"
LEVELS = [
    {"name": "minor", "description": "Tangential.", "weight": 1},
    {"name": "notable", "description": "A useful report.", "weight": 2},
    {"name": "major", "description": "A significant incident or policy change.", "weight": 3},
    {"name": "critical", "description": "Deaths, mass stranding, deportation, a court order.", "weight": 4},
]


def article(n: int, title: str, hours: float, **over):
    when = datetime.now(UTC) - timedelta(hours=hours)
    base = {"guid": f"g{n}", "title": title, "link": f"https://paper.example/{n}", "source": "The Hindu", "published_at": when.isoformat(timespec="seconds").replace("+00:00", "Z"), "description": f"About {title}", "image_url": f"https://img.example/{n}.jpg", "lang": "en", "feed_key": "migrant"}
    return {**base, **over}


def config(**over):
    cfg = {
        "source": {"kind": "feed", "feeds": [{"key": "migrant", "url": "https://news.google.com/rss/search?q=migrant", "lang": "en"}]},
        "filters": {"sinceDays": 3, "newOnly": True, "limit": 50},
        "prompt": "You pick news for S&B, which works for Indian migrant workers: deaths, difficulties, stranded workers.",
        "labels": [{"name": "relevant", "description": "About Indian migrant workers.", "route": "publish"}, {"name": "irrelevant", "description": "Anything else."}],
        "importance": {"prompt": "How much does this story matter to S&B?", "levels": LEVELS},
        "decider": "model", "defaultThreshold": 0.6, "batchSize": 1,
    }
    cfg.update(over)
    return cfg


async def make_news(rig, workspaces=("sb",), **over):
    await connect(rig)
    r = await rig.client.post(BASE, json={"name": "S&B news", "description": "", "config": config(**over), "workspaces": list(workspaces)}, headers=headers())
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    await choose(rig, agent=f"/v1/agent/agents/{wid}")
    return wid


async def top(rig, query="", who=None):
    return (await rig.client.get(f"{NEWS}?{query}", headers=who or headers())).json()


def test_a_critical_story_from_today_beats_a_notable_one_and_both_fade():
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    iso = lambda h: (now - timedelta(hours=h)).isoformat().replace("+00:00", "Z")  # noqa: E731
    story = lambda weight, hours, rel=0.9: {"weight": weight, "publishedAt": iso(hours), "relevance": rel}  # noqa: E731
    assert rank_news(story(4, 1), now) > rank_news(story(2, 1), now)
    assert rank_news(story(4, 96), now) < rank_news(story(2, 1), now)  # last week's critical story sinks below today's notable one
    assert rank_news(story(3, 1), now) > rank_news(story(3, 40), now)
    assert rank_news(story(3, 1, 0.95), now) > rank_news(story(3, 1, 0.6), now)
    assert rank_news({}, now) > 0  # a story with nothing known about it is still ranked


async def test_news_is_filtered_for_relevance_then_graded_and_ranked_newest_important_first(rig):
    rig.data.articles = [article(1, "Workers stranded in Kuwait appeal to embassy", 2), article(2, "Cricket: India win the toss", 3), article(3, "Kerala migrant worker dies in Gulf accident", 30), article(4, "Union budget doubles allocation", 5)]
    wid = await make_news(rig)
    # relevance for the four, newest first (1, 2, the budget, 3), then importance for the two that passed
    rig.script += [answer("relevant"), answer("irrelevant"), answer("irrelevant"), answer("relevant"), answer("critical"), answer("notable")]
    run = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert run["counts"]["published"] == 2 and run["counts"]["irrelevant"] == 2 and "on the news page" in run["summary"]
    page = await top(rig, "workspace=sb")
    assert [s["title"] for s in page["items"]] == ["Workers stranded in Kuwait appeal to embassy", "Kerala migrant worker dies in Gulf accident"]
    assert page["sort"] == "importance"
    first = page["items"][0]
    assert (first["importance"], first["weight"], first["source"], first["link"], first["imageUrl"], first["feed"]) == ("critical", 4, "The Hindu", "https://paper.example/1", "https://img.example/1.jpg", "migrant")
    assert page["total"] == 2
    assert "Cricket" not in str(page)
    # the second question was only asked about the two that passed the first
    assert len([c for c in rig.llm_log if "How much does this story matter" in c["system"]]) == 2
    # and everything is in the run's record, with the cost
    assert (await rig.client.get(f"{BASE}/{wid}/executions", headers=headers())).json()["items"][0]["counts"]["published"] == 2


async def test_what_was_read_is_not_read_again_and_a_big_backlog_is_worked_through_in_runs(rig):
    rig.data.articles = [article(1, "Story one", 1), article(2, "Story two", 2), article(3, "Story three", 3)]
    wid = await make_news(rig, filters={"sinceDays": 3, "newOnly": True, "limit": 2}, importance=None)
    rig.script += [answer("relevant"), answer("relevant")]
    first = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert first["counts"]["items"] == 2 and first["stats"]["remaining"] == 1 and first["stats"]["inRange"] == 3
    rig.script += [answer("relevant")]
    second = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert second["counts"]["items"] == 1 and second["stats"]["remaining"] == 0
    third = (await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())).json()
    assert third["counts"]["items"] == 0  # nothing new: no page is read, no model is asked
    assert [s["title"] for s in (await top(rig))["items"]] == ["Story one", "Story two", "Story three"]
    assert rig.data.feeds_asked[0] == ["https://news.google.com/rss/search?q=migrant"]


async def test_without_a_second_question_stories_are_published_at_a_middling_weight(rig):
    rig.data.articles = [article(1, "Story one", 1)]
    wid = await make_news(rig, importance=None)
    rig.script += [answer("relevant")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    item = (await top(rig))["items"][0]
    assert item["importance"] is None and item["weight"] == 2


async def test_a_story_can_be_taken_off_and_the_page_is_per_workspace_and_private(rig):
    rig.data.articles = [article(1, "Story one", 1), article(2, "Old story", 24 * 10)]
    wid = await make_news(rig, importance=None, filters={"sinceDays": 30, "newOnly": True, "limit": 50})
    rig.script += [answer("relevant"), answer("relevant")]
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    assert [s["title"] for s in (await top(rig, "days=7"))["items"]] == ["Story one"]  # the page shows the last week by default
    assert len((await top(rig, "days=30"))["items"]) == 2
    assert (await top(rig, "workspace=fsa"))["items"] == [] and len((await top(rig, "workspace=sb"))["items"]) == 1
    assert (await top(rig, who=headers("b@example.com", "u2")))["items"] == []
    item = (await top(rig))["items"][0]
    other = headers("b@example.com", "u2")
    assert (await rig.client.post(f"{NEWS}/hide", json={"agentId": wid, "itemId": item["itemId"]}, headers=other)).status_code == 404
    assert (await rig.client.post(f"{NEWS}/hide", json={"agentId": wid, "itemId": item["itemId"]}, headers=headers())).status_code == 204
    assert (await top(rig))["items"] == []
    assert (await rig.client.post(f"{NEWS}/hide", json={"agentId": wid, "itemId": item["itemId"]}, headers=headers())).status_code == 404
    assert (await rig.client.get(NEWS)).status_code == 401


async def test_the_limit_is_twenty_by_default(rig):
    rig.data.articles = [article(i, f"Story {i}", i) for i in range(1, 31)]
    wid = await make_news(rig, importance=None, filters={"sinceDays": 3, "newOnly": True, "limit": 50}, batchSize=10)
    from tests.test_mail_filter import batch_answer  # noqa: PLC0415

    rig.script += [batch_answer(*[(i, "relevant", 0.9) for i in range(1, 11)])] * 3
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    page = await top(rig)
    assert len(page["items"]) == 20 and page["total"] == 30
    assert len((await top(rig, "limit=5"))["items"]) == 5
    assert (await rig.client.get(f"{NEWS}?limit=50", headers=headers())).status_code == 422  # never more than twenty at a time
    assert len((await top(rig, "sort=latest"))["items"]) == 20


async def test_a_news_setup_needs_feeds_and_sensible_levels(rig):
    def post(**over):
        return rig.client.post(BASE, json={"name": "x", "description": "", "config": config(**over)}, headers=headers())

    assert (await post(source={"kind": "feed", "feeds": []})).status_code == 422
    assert (await post(source={"kind": "feed", "feeds": [{"key": "a", "url": "ftp://x"}]})).status_code == 422
    assert (await post(importance={"prompt": "p", "levels": LEVELS[:1]})).status_code == 422
    assert (await post(scheduleMinutes=3)).status_code == 422
    assert (await post(scheduleMinutes=30)).status_code == 201


async def test_a_schedule_runs_it_now_and_then_every_so_often():
    runs = []

    async def trigger():
        runs.append(1)

    m = ScheduleManager(seconds_per_minute=0.01)
    m.ensure("w", 10, trigger)  # every "10 minutes" = 0.1 s here
    assert await until(lambda: len(runs) >= 3, tries=100)
    s = m.status("w")
    assert s["scheduled"] and s["everyMinutes"] == 10 and s["lastRunAt"] and s["nextRunAt"] and s["error"] is None
    m.stop("w")
    count = len(runs)
    await asyncio.sleep(0.25)
    assert len(runs) == count and m.status("w")["scheduled"] is False
    await m.close()


async def test_a_failed_start_is_reported_and_the_schedule_goes_on():
    attempts = []

    async def trigger():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("nothing to run on")

    m = ScheduleManager(seconds_per_minute=0.01)
    m.ensure("w", 10, trigger)
    assert await until(lambda: len(attempts) >= 2, tries=100)
    assert m.status("w")["error"] is None and m.status("w")["runs"] >= 1  # the second try worked and cleared it
    await m.close()


async def test_an_agent_with_a_schedule_runs_by_itself_and_stops_when_the_schedule_is_removed(rig):
    rig.app.state.schedules.unit = 0.01
    rig.data.articles = [article(1, "Story one", 1)]
    await connect(rig)
    rig.script += [answer("relevant"), answer("critical")]
    r = await rig.client.post(BASE, json={"name": "News", "description": "", "config": config(scheduleMinutes=10), "workspaces": ["sb"]}, headers=headers())
    wid = r.json()["id"]
    await choose(rig, agent=f"/v1/agent/agents/{wid}")  # the first start had no model and said so; the next one works
    assert await until(lambda: len(rig.data.feeds_asked) >= 1, tries=200)
    for _ in range(100):
        if (await top(rig))["items"]:
            break
        await asyncio.sleep(0.03)
    assert [s["title"] for s in (await top(rig))["items"]] == ["Story one"]
    status = (await rig.client.get(f"{BASE}/{wid}/listen", headers=headers())).json()
    assert status["schedule"]["scheduled"] is True and status["schedule"]["everyMinutes"] == 10
    await rig.client.put(f"{BASE}/{wid}", json={"name": "News", "description": "", "config": config(scheduleMinutes=None)}, headers=headers())
    assert (await rig.client.get(f"{BASE}/{wid}/listen", headers=headers())).json()["schedule"]["scheduled"] is False


async def test_the_same_stories_in_two_rankings_most_important_and_latest(rig):
    rig.data.articles = [article(1, "Newest, notable", 1), article(2, "Older, critical", 20), article(3, "Middle, major", 6), article(4, "Oldest, critical", 40)]
    wid = await make_news(rig)
    rig.script += [answer("relevant")] * 4 + [answer("notable"), answer("major"), answer("critical"), answer("critical")]  # graded newest first: 1, 3, 2, 4
    await rig.client.post(f"{BASE}/{wid}/run", json={}, headers=headers())
    important = await top(rig, "sort=importance")
    assert [s["title"] for s in important["items"]] == ["Older, critical", "Oldest, critical", "Middle, major", "Newest, notable"]  # by weight, newer first among equals
    latest = await top(rig, "sort=latest")
    assert [s["title"] for s in latest["items"]] == ["Newest, notable", "Middle, major", "Older, critical", "Oldest, critical"]
    assert latest["sort"] == "latest" and (await top(rig))["sort"] == "importance"
    assert (await rig.client.get(f"{NEWS}?sort=best", headers=headers())).status_code == 422
