"""Tests for the Libgen scraper: results parsing and download-link resolution."""

import importlib.util
import threading
from unittest.mock import patch

import pytest
from urllib3.util.request import ACCEPT_ENCODING

from shelfmark.release_sources.libgen import scraper
from tests.libgen import sample_html as html


def test_parse_results_extracts_all_md5_rows():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert records is not None
    assert [r.id for r in records] == [html.MD5_A, html.MD5_B, html.MD5_C, html.MD5_D]


def test_parse_results_full_row_fields():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    a = records[0]
    assert a.title == "One Piece, Vol. 1"
    assert a.author == "Eiichiro Oda"
    assert a.format == "epub"
    assert a.size == "180 MB"  # &nbsp; normalized to a plain space
    assert a.language == "en"
    assert a.source == "libgen"
    assert a.source_url == f"https://libgen.li/ads.php?md5={html.MD5_A}"


def test_parse_results_cover_column_becomes_preview():
    records = scraper._parse_results(html.SEARCH_HTML_WITH_COVERS, "https://libgen.li")
    assert records is not None
    full, _compact = records
    assert full.preview == f"https://libgen.li/covers/467000/{html.MD5_A}_small.jpg"
    assert full.title == "One Piece, Vol. 1"
    assert full.author == "Eiichiro Oda"
    assert full.language == "en"
    assert full.format == "epub"
    assert full.size == "180 MB"


def test_parse_results_empty_cover_cell_is_not_the_title():
    records = scraper._parse_results(html.SEARCH_HTML_WITH_COVERS, "https://libgen.li")
    compact = records[1]
    assert compact.preview is None
    assert compact.title == "One Piece 515"
    assert compact.format == "cbr"
    assert compact.size == "6 MB"


def test_search_libgen_requests_covers():
    calls = []

    def fake_fetch(url, timeout=(5, 15)):
        calls.append(url)
        return html.SEARCH_HTML

    with patch.object(scraper, "fetch_page", side_effect=fake_fetch):
        scraper.search_libgen("one piece", ["https://libgen.li"], max_results=25)
    assert calls[0].endswith("&covers=on")


def test_parse_results_md5_scoped_to_mirrors_cell():
    # Row A's Title cell carries a decoy md5-shaped cover URL that appears BEFORE the real
    # md5 in document order; the parser must resolve to the Mirrors-cell md5, not the decoy.
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert records[0].id == html.MD5_A
    assert html.DECOY_MD5 not in {r.id for r in records}


def test_parse_results_offtopic_row_survives():
    # No relevance filter: a row that merely name-drops the query is kept, same as AA.
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert any(r.id == html.MD5_B and r.format == "cbr" for r in records)


def test_parse_results_compact_rows_have_no_author_or_language():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    c = next(r for r in records if r.id == html.MD5_C)
    assert c.title == "One Piece 515"
    assert c.format == "cbr"
    assert c.size == "6 MB"
    assert c.author is None
    assert c.language is None


def test_parse_results_comics_not_dropped_by_format():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert {"cbr", "cbz"} <= {r.format for r in records}


def test_parse_results_no_table_returns_none():
    assert scraper._parse_results(html.NO_TABLE_HTML, "https://libgen.li") is None


def test_parse_results_no_matches_page_returns_empty_list():
    # Before this, a no-match search counted as a failed mirror and its cooldown then
    # refused every libgen search for two minutes.
    assert scraper._parse_results(html.NO_MATCHES_HTML, "https://libgen.li") == []


def test_parse_results_empty_table_returns_empty_list():
    result = scraper._parse_results(html.EMPTY_TABLE_HTML, "https://libgen.li")
    assert result == []
    assert result is not None  # distinct from the no-table case


def test_resolve_download_url_extracts_keyed_get():
    url = scraper.resolve_download_url(html.ADS_HTML, "https://libgen.li")
    assert url == f"https://libgen.li/get.php?md5={html.MD5_A}&key={html.GET_KEY}"


def test_resolve_download_url_missing_get_returns_none():
    assert scraper.resolve_download_url(html.ADS_HTML_NO_GET, "https://libgen.li") is None


def test_fetch_page_sends_same_origin_referer():
    # libgen.li's ads.php returns an empty 200 without a Referer; fetch_page must send a
    # same-origin one or every download-page fetch comes back blank.
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "<html>ok</html>"

    def fake_get(url, **kwargs):
        captured["headers"] = kwargs["headers"]
        return FakeResponse()

    with (
        patch.object(scraper.requests, "get", side_effect=fake_get),
        patch.object(scraper.network, "get_proxies", return_value=None),
        patch.object(scraper.network, "get_ssl_verify", return_value=True),
    ):
        result = scraper.fetch_page("https://libgen.li/ads.php?md5=abc")

    assert result == "<html>ok</html>"
    assert captured["headers"]["Referer"] == "https://libgen.li/"


def test_fetch_page_asks_only_for_encodings_requests_can_decode():
    # urllib3 decodes Brotli only with a Brotli package. Without it, Cloudflare-fronted
    # mirrors (libgen.la, libgen.gl) answered "br" bodies that parsed as a dead mirror.
    accept = scraper.downloader.DOWNLOAD_HEADERS["Accept-Encoding"]
    assert accept == ACCEPT_ENCODING

    has_brotli = bool(importlib.util.find_spec("brotli") or importlib.util.find_spec("brotlicffi"))
    asks_for_br = "br" in [encoding.strip() for encoding in accept.split(",")]
    assert asks_for_br == has_brotli


def test_search_libgen_falls_through_dead_mirror():
    calls = []

    def fake_fetch(url, timeout=(5, 15)):
        calls.append(url)
        return None if "dead" in url else html.SEARCH_HTML

    with patch.object(scraper, "fetch_page", side_effect=fake_fetch):
        records = scraper.search_libgen(
            "one piece", ["https://dead.example", "https://libgen.li"], max_results=25
        )
    assert len(records) == 4
    assert len(calls) == 2  # dead mirror tried first, then the live one


def test_search_libgen_all_mirrors_dead_raises():
    with (
        patch.object(scraper, "fetch_page", return_value=None),
        pytest.raises(scraper.LibgenUnavailableError, match="https://a, https://b"),
    ):
        scraper.search_libgen("q", ["https://a", "https://b"], max_results=25)


def test_search_libgen_empty_table_is_an_answer():
    with patch.object(scraper, "fetch_page", return_value=html.EMPTY_TABLE_HTML) as fetch:
        assert scraper.search_libgen("q", ["https://a", "https://b"], max_results=25) == []
    assert fetch.call_count == 1


def test_search_libgen_no_match_keeps_the_mirror_healthy():
    # A search with no match (or a page past the last) must not start the cooldown:
    # it used to, and the next search then failed without asking any mirror.
    with patch.object(scraper, "fetch_page", return_value=html.NO_MATCHES_HTML):
        assert scraper.search_libgen("9780691151632", ["https://a"], max_results=25, page=2) == []
    with patch.object(scraper, "fetch_page", return_value=html.SEARCH_HTML) as fetch:
        assert len(scraper.search_libgen("laws of truth", ["https://a"], max_results=25)) == 4
    assert fetch.call_count == 1


def test_search_libgen_skips_a_mirror_that_failed_recently():
    calls = []

    def fake_fetch(url, timeout=(5, 15)):
        calls.append(url)
        return None if "dead" in url else html.SEARCH_HTML

    mirrors = ["https://dead.example", "https://libgen.li"]
    with patch.object(scraper, "fetch_page", side_effect=fake_fetch):
        scraper.search_libgen("first", mirrors, max_results=25)
        calls.clear()
        scraper.search_libgen("second", mirrors, max_results=25)
    assert [url.split("/index.php")[0] for url in calls] == ["https://libgen.li"]


def test_search_libgen_fails_fast_when_every_mirror_is_cooling():
    with patch.object(scraper, "fetch_page", return_value=None) as fetch:
        with pytest.raises(scraper.LibgenUnavailableError):
            scraper.search_libgen("q", ["https://a"], max_results=25)
        with pytest.raises(scraper.LibgenUnavailableError, match="failed in the last"):
            scraper.search_libgen("q", ["https://a"], max_results=25)
    assert fetch.call_count == 1


def test_search_libgen_retries_a_mirror_after_its_cooldown():
    with patch.object(scraper, "fetch_page", return_value=None):
        with pytest.raises(scraper.LibgenUnavailableError):
            scraper.search_libgen("q", ["https://a"], max_results=25)
    scraper._mirror_failed_at["https://a"] -= scraper._MIRROR_COOLDOWN_SECONDS
    with patch.object(scraper, "fetch_page", return_value=html.SEARCH_HTML):
        assert len(scraper.search_libgen("q", ["https://a"], max_results=25)) == 4


def test_search_libgen_hung_mirror_does_not_block_the_next():
    release = threading.Event()

    def fake_fetch(url, timeout=(5, 15)):
        if "hung" in url:
            release.wait(5)
            return None
        return html.SEARCH_HTML

    try:
        with (
            patch.object(scraper, "fetch_page", side_effect=fake_fetch),
            patch.object(scraper, "_MIRROR_STAGGER_SECONDS", 0.05),
        ):
            records = scraper.search_libgen(
                "q", ["https://hung.example", "https://libgen.li"], max_results=25
            )
    finally:
        release.set()
    assert len(records) == 4


def test_search_libgen_does_not_ask_the_next_mirror_when_the_first_answers():
    with patch.object(scraper, "fetch_page", return_value=html.SEARCH_HTML) as fetch:
        scraper.search_libgen("q", ["https://a", "https://b"], max_results=25)
    assert fetch.call_count == 1


def test_search_libgen_asks_for_a_later_page():
    calls = []

    def fake_fetch(url, timeout=(5, 15)):
        calls.append(url)
        return html.SEARCH_HTML

    with patch.object(scraper, "fetch_page", side_effect=fake_fetch):
        scraper.search_libgen("one piece", ["https://libgen.li"], max_results=25, page=3)
    assert calls[0].endswith("&covers=on&page=3")
