from app.domain.search import clean_terms, escape, files_query, parse_terms
from httpx import ASGITransport, AsyncClient

from tests.conftest import FakeGateway, build, doc

# --- the rules ------------------------------------------------------------------------------------------------


def test_words_and_quoted_phrases():
    assert parse_terms('labour  "WP 1290/2026" NHAI') == ["labour", "WP 1290/2026", "NHAI"]
    assert parse_terms("   ") == [] and parse_terms("") == []


def test_every_term_must_match_and_folders_and_trash_are_left_out():
    q = files_query(["labour", "WP 1290/2026"])
    assert q == (
        "fullText contains 'labour' and fullText contains '\"WP 1290/2026\"' and trashed = false "
        "and mimeType != 'application/vnd.google-apps.folder'")
    assert "contains '\"TUMWU-2026-001\"'" in files_query(["TUMWU-2026-001"], phrases=True)


def test_quotes_in_what_people_type_cannot_break_out_of_the_query():
    assert escape("O'Brien \\ x") == "O\\'Brien \\\\ x"
    # a term with spaces becomes one quoted phrase, and every quote inside it is escaped
    q = files_query(["x' or name contains 'y"])
    assert q.startswith("fullText contains '\"x\\' or name contains \\'y\"' and ")
    assert files_query(["it's"]).startswith("fullText contains 'it\\'s' and ")


def test_suggestion_terms_are_tidied():
    assert clean_terms(["TUMWU-2026-001", "  tumwu-2026-001 ", "ab", "", "Alur  84 Workers", None]) == ["TUMWU-2026-001", "Alur 84 Workers"]
    assert len(clean_terms([f"term {i}" for i in range(30)])) == 8


# --- who may use it ------------------------------------------------------------------------------------------------


async def test_requires_sign_in(client):
    assert (await client.get("/v1/drive/search?q=x")).status_code == 401
    assert (await client.get("/v1/drive/status")).status_code == 401


async def test_only_the_listed_email_may_search(client, stranger, gateway):
    assert (await client.get("/v1/drive/search?q=letter", headers=stranger)).status_code == 403
    assert (await client.post("/v1/drive/suggest", json={"terms": ["letter"]}, headers=stranger)).status_code == 403
    assert gateway.queries == []  # nothing was sent to Google on their behalf


async def test_nobody_may_search_until_someone_is_listed(gateway, owner):
    async with AsyncClient(transport=ASGITransport(app=build(gateway, allowed=())), base_url="http://test") as c:
        assert (await c.get("/v1/drive/search?q=x", headers=owner)).status_code == 403
        assert (await c.get("/v1/drive/status", headers=owner)).json()["available"] is False


async def test_email_comparison_ignores_case(gateway):
    from tests.conftest import make_token

    async with AsyncClient(transport=ASGITransport(app=build(gateway, allowed=("Owner@Example.com",))), base_url="http://test") as c:
        r = await c.get("/v1/drive/search?q=letter", headers={"Authorization": f"Bearer {make_token('OWNER@example.COM')}"})
        assert r.status_code == 200


# --- status ----------------------------------------------------------------------------------------------------------


async def test_status_says_whether_it_can_be_used(client, owner, stranger):
    assert (await client.get("/v1/drive/status", headers=owner)).json() == {"available": True, "folder": "Letters"}
    denied = (await client.get("/v1/drive/status", headers=stranger)).json()
    assert denied["available"] is False and "reason" in denied


async def test_status_when_google_is_not_connected_yet(owner):
    async with AsyncClient(transport=ASGITransport(app=build(FakeGateway(configured=False))), base_url="http://test") as c:
        s = (await c.get("/v1/drive/status", headers=owner)).json()
        assert s["available"] is False and "isn't connected" in s["reason"]
        r = await c.get("/v1/drive/search?q=x", headers=owner)
        assert r.status_code == 503 and r.json()["error"]["code"] == "drive_not_connected"


# --- searching ---------------------------------------------------------------------------------------------------------


async def test_search_returns_only_file_details(client, owner, gateway):
    r = await client.get("/v1/drive/search", params={"q": "nhai"}, headers=owner)
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["items"]]
    assert names == ["Letter to NHAI.docx", "TUMWU-2026-001 reply from NHAI.pdf"]
    first = r.json()["items"][0]
    assert set(first) == {"id", "name", "mimeType", "modifiedTime", "webViewLink"}
    assert "fullText contains 'nhai'" in gateway.queries[0]["query"] and gateway.queries[0]["newest_first"] is False


async def test_every_word_has_to_match(client, owner):
    assert len((await client.get("/v1/drive/search", params={"q": "nhai reply"}, headers=owner)).json()["items"]) == 1
    assert (await client.get("/v1/drive/search", params={"q": "nhai zzz"}, headers=owner)).json()["items"] == []


async def test_no_words_lists_the_newest_files_and_paging_is_passed_through(client, owner, gateway):
    await client.get("/v1/drive/search", params={"limit": 2, "pageToken": "abc"}, headers=owner)
    assert gateway.queries[-1] == {"query": "trashed = false and mimeType != 'application/vnd.google-apps.folder'", "limit": 2,
                                   "page_token": "abc", "newest_first": True}
    r = await client.get("/v1/drive/search", params={"limit": 1}, headers=owner)
    assert r.json()["nextPageToken"] == "next-page"


async def test_bad_requests(client, owner):
    assert (await client.get("/v1/drive/search", params={"q": "x" * 301}, headers=owner)).status_code == 422
    assert (await client.get("/v1/drive/search", params={"limit": 0}, headers=owner)).status_code == 422
    assert (await client.get("/v1/drive/search", params={"limit": 99}, headers=owner)).status_code == 422


async def test_google_being_down_is_reported_not_crashed(owner):
    async with AsyncClient(transport=ASGITransport(app=build(FakeGateway(fail="boom"))), base_url="http://test") as c:
        r = await c.get("/v1/drive/search?q=x", headers=owner)
        assert r.status_code == 502 and r.json()["error"]["code"] == "drive_unavailable"
        assert "boom" not in r.text  # Google's details stay in the logs


# --- suggestions ---------------------------------------------------------------------------------------------------------


async def test_suggest_ranks_files_matching_more_details_first(owner):
    files = [doc("a", "TUMWU-2026-001 alur 84 workers letter.pdf", "2026-01-01T00:00:00.000Z"),
             doc("b", "TUMWU-2026-001 only.pdf", "2026-06-01T00:00:00.000Z"),
             doc("c", "alur 84 workers only.pdf", "2026-02-01T00:00:00.000Z"),
             doc("d", "something else.pdf")]
    async with AsyncClient(transport=ASGITransport(app=build(FakeGateway(files))), base_url="http://test") as c:
        r = await c.post("/v1/drive/suggest", headers=owner, json={"terms": ["TUMWU-2026-001", " alur  84 workers", "zz", "tumwu-2026-001"]})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [i["id"] for i in items] == ["a", "b", "c"]  # two matches, then the newer single match, then the older
        assert items[0]["matched"] == ["TUMWU-2026-001", "alur 84 workers"]
        assert items[1]["matched"] == ["TUMWU-2026-001"]


async def test_suggest_searches_each_detail_as_an_exact_phrase(client, owner, gateway):
    await client.post("/v1/drive/suggest", headers=owner, json={"terms": ["TUMWU-2026-001", "WP 1290/2026"]})
    queries = [q["query"] for q in gateway.queries]
    assert any("contains '\"TUMWU-2026-001\"'" in q for q in queries) and any("contains '\"WP 1290/2026\"'" in q for q in queries)


async def test_suggest_with_nothing_useful_asks_nothing(client, owner, gateway):
    assert (await client.post("/v1/drive/suggest", headers=owner, json={"terms": ["", "ab"]})).json() == {"items": []}
    assert gateway.queries == []
    assert (await client.post("/v1/drive/suggest", headers=owner, json={"terms": ["x"] * 30})).status_code == 422
