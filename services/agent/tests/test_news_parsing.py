import base64
from datetime import UTC, datetime

from app.infrastructure.news import clean_text, extract_meta, old_style_url, parse_rss

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel><title>x</title>
<item><title>Stranded Telangana workers in Kuwait await rescue - The Hindu</title><link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link><guid isPermaLink="false">CBMiAAA</guid>
<pubDate>Sun, 01 Nov 2026 09:00:00 GMT</pubDate><description>&lt;a href="x"&gt;Stranded Telangana workers in Kuwait await rescue&lt;/a&gt;&amp;nbsp;&amp;nbsp;The Hindu</description><source url="https://www.thehindu.com">The Hindu</source></item>
<item><title><![CDATA[Gulf labour: new advisory - Deccan Herald]]></title><link>https://news.google.com/rss/articles/CBMiBBB</link><guid>CBMiBBB</guid><pubDate>Wed, 28 Oct 2026 09:00:00 GMT</pubDate><source url="https://www.deccanherald.com">Deccan Herald</source></item>
<item><title>Newer one</title><link>https://example.com/a</link><guid>g3</guid><pubDate>Sun, 01 Nov 2026 11:00:00 GMT</pubDate><media:content url="https://example.com/p.jpg"/></item>
<item><title></title><link>https://example.com/empty</link></item>
</channel></rss>"""


def test_the_items_of_a_feed_newest_first_and_no_older_than_asked():
    items = parse_rss(RSS, "en", "migrant", NOW, max_age_days=3)
    assert [i["guid"] for i in items] == ["g3", "CBMiAAA"]  # the 28 October one is too old; the empty one is no item
    first = items[1]
    assert first["title"] == "Stranded Telangana workers in Kuwait await rescue"  # the paper is taken off the headline
    assert (first["source"], first["source_url"], first["published_at"], first["lang"], first["feed_key"]) == ("The Hindu", "https://www.thehindu.com", "2026-11-01T09:00:00Z", "en", "migrant")
    assert first["description"] is None  # Google's snippet only repeats the headline
    assert items[0]["image_url"] == "https://example.com/p.jpg" and items[0]["source"] == "Google News"
    assert len(parse_rss(RSS, "en", "k", NOW, max_age_days=None)) == 3


def test_a_feed_that_is_not_xml_gives_nothing():
    assert parse_rss("<html>nope", "en", "k", NOW) == []
    assert parse_rss("", "en", "k", NOW) == []


def test_the_description_and_picture_come_from_the_pages_own_tags():
    page = '<head><meta property="og:description" content="Dozens of workers are stranded &amp; waiting for the embassy to act."><meta content="https://img.example/p.webp" property="og:image"></head>'
    assert extract_meta(page) == ("Dozens of workers are stranded & waiting for the embassy to act.", "https://img.example/p.webp")
    other = '<meta name="description" content="A plain description of the story, long enough."><meta name="twitter:image" content="//cdn.example/x.jpg">'
    assert extract_meta(other) == ("A plain description of the story, long enough.", "https://cdn.example/x.jpg")


def test_a_page_with_no_tags_gives_its_first_real_paragraphs_and_googles_own_pictures_are_skipped():
    page = (
        '<meta property="og:image" content="https://news.google.com/logo.png"><p>Share</p>'
        "<p>Hyderabad: Thirty-two workers from Telangana who went to Kuwait on promises of good wages say their employer has kept their passports and stopped paying them.</p>"
        "<p>The embassy said it was in touch with the employer and that a team would visit the camp this week to arrange for their return home.</p>"
    )
    description, image = extract_meta(page)
    assert description and description.startswith("Hyderabad: Thirty-two workers") and "embassy" in description and image is None


def test_older_google_links_carry_the_address_inside_them():
    token = base64.urlsafe_b64encode(b"\x08\x13\x22\x25https://www.thehindu.com/news/article123.ece\xd2\x01\x00").decode().rstrip("=")
    assert old_style_url(token) == "https://www.thehindu.com/news/article123.ece"
    assert old_style_url("not-base64-at-all!!") is None
    assert old_style_url(base64.urlsafe_b64encode(b"\x08\x13no address here").decode()) is None


def test_clean_text():
    assert clean_text("<b>Hi</b>&nbsp;&amp;  there\n now") == "Hi & there now"
