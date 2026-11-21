"""Headlines, three at a time, each with a button that opens the article and a button for the next three."""

from datetime import UTC, datetime, timedelta

from app.application.headlines import PAGE, HeadlineDesk, page

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
WHO = type("W", (), {"user_id": "u1", "email": "a@b.c", "name": ""})()


def stories(n):
    return [{"title": f"Story {i}", "source": "The Hindu", "link": f"https://paper.example/{i}", "description": "labour codes" if i % 2 else "other", "publishedAt": (NOW - timedelta(hours=i)).isoformat().replace("+00:00", "Z")} for i in range(1, n + 1)]


class FakeNews:
    def __init__(self, items):
        self.items, self.asked = items, []

    async def top(self, who, workspace, limit, days, sort):
        self.asked.append((workspace, limit, days, sort))
        return {"items": self.items[:limit]}


def test_three_headlines_come_with_an_open_button_each_and_a_button_for_the_next_three():
    text, rows = page(stories(8), 0, "importance", "sb", NOW)
    assert text.startswith("📰 Headlines 1–3 of 8 · most important first") and "1. Story 1\n   The Hindu · 1h ago" in text and "3. Story 3" in text and "Story 4" not in text
    assert [r[0]["url"] for r in rows[:3]] == [f"https://paper.example/{i}" for i in (1, 2, 3)]
    assert rows[3][0]["callback_data"] == "nw:m:i:3:sb" and rows[3][1]["callback_data"] == "nw:x:0" and rows[3][0]["text"] == "📰 3 more"
    text, rows = page(stories(8), 6, "latest", None, NOW)
    assert "7–8 of 8 · latest first" in text and [len(r) for r in rows] == [1, 1, 1]  # the last page has no "more", only "enough"
    assert page(stories(2), 5, "importance", None, NOW) == ("That's all the headlines I have for now.", [])
    unsafe = [{**stories(1)[0], "link": "javascript:alert(1)"}]
    assert all("url" not in b for row in page(unsafe, 0, "importance", None, NOW)[1] for b in row)  # only real web links become buttons


async def test_the_desk_sends_three_filters_by_topic_and_walks_on_when_more_is_pressed():
    sent = []

    async def send(who, text, buttons, channel_id):
        sent.append((text, buttons, channel_id))

    news = FakeNews(stories(7))
    desk = HeadlineDesk(news, send, lambda: NOW)
    out = await desk.show(WHO, workspace="sb", channel_id="ch1")
    assert out == {"shown": 3, "left": 4, "total": 7} and sent[0][2] == "ch1" and news.asked[0] == ("sb", 20, 3, "importance")
    topical = await desk.show(WHO, query="labour")
    assert topical["total"] == 4 and "Story 2" not in sent[-1][0]
    assert (await desk.show(WHO, query="nothing like this"))["total"] == 0
    next_text, next_rows = await desk.press(WHO, "m", "i:3:sb")
    assert next_text.startswith("📰 Headlines 4–6 of 7") and next_rows[-1][0]["callback_data"] == "nw:m:i:6:sb"
    assert "7 of 7" in (await desk.press(WHO, "m", "i:6:sb"))[0]
    assert (await desk.press(WHO, "x", "0")).startswith("👍") and PAGE == 3
