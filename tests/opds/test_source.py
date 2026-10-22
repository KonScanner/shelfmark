"""Tests for the OPDS release source and its download handler."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.opds import source as opds
from shelfmark.release_sources.opds.handler import OPDSHandler
from shelfmark.release_sources.opds.source import OPDSSource

_SOURCE = "shelfmark.release_sources.opds.source"
_HANDLER = "shelfmark.release_sources.opds.handler"
_SUPPORTED_FORMATS = "shelfmark.release_sources.direct_download.common.get_supported_formats"
_WANTED = ["epub", "fb2", "mobi", "pdf"]

_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">'
)

# Flibusta-style: each search entry carries its files.
FLIBUSTA_SEARCH = (
    _HEAD + '<link href="/opds/opensearch?searchTerm=x&amp;pageNumber=1" rel="next"'
    ' type="application/atom+xml;profile=opds-catalog"/>'
    "<entry><title>Дюна</title><author><name>Герберт Фрэнк</name></author>"
    "<dc:language>ru</dc:language><dc:issued>1965</dc:issued>"
    '<link href="/a/1" rel="related" type="application/atom+xml"/>'
    '<link href="/b/7/fb2" rel="http://opds-spec.org/acquisition/open-access"'
    ' type="application/fb2+zip"/>'
    '<link href="/b/7/epub" rel="http://opds-spec.org/acquisition/open-access"'
    ' type="application/epub+zip"/>'
    '<link href="/b/7/html" rel="http://opds-spec.org/acquisition/open-access"'
    ' type="application/html+zip"/>'
    '<link href="/b/7" rel="alternate" type="text/html"/>'
    "</entry></feed>"
)

# Gutenberg-style: the search lists navigation entries; books link to their own feeds.
GUTENBERG_SEARCH = (
    _HEAD + "<entry><title>Subjects</title>"
    '<link type="application/atom+xml;profile=opds-catalog" rel="subsection"'
    ' href="/ebooks/subjects/search.opds/?query=odyssey"/></entry>'
    "<entry><title>The Odyssey</title><content>Homer</content>"
    '<link type="application/atom+xml;profile=opds-catalog" rel="subsection"'
    ' href="/ebooks/1727.opds"/>'
    '<link type="image/png" rel="http://opds-spec.org/image/thumbnail" href="data:image/png;x"/>'
    "</entry></feed>"
)
GUTENBERG_BOOK = (
    _HEAD + "<entry><title>The Odyssey</title><author><name>Homer</name></author>"
    "<dc:language>en</dc:language>"
    '<link type="application/epub+zip" rel="http://opds-spec.org/acquisition" length="380973"'
    ' href="https://www.gutenberg.org/ebooks/1727.epub.noimages"/></entry>'
    "<entry><title>The Odyssey</title>"
    '<link type="application/epub+zip" rel="http://opds-spec.org/acquisition" length="445840"'
    ' href="https://www.gutenberg.org/ebooks/1727.epub3.images"/>'
    '<link type="application/x-mobipocket-ebook" rel="http://opds-spec.org/acquisition"'
    ' href="https://www.gutenberg.org/ebooks/1727.kf8.images"/>'
    '<link type="image/jpeg" rel="http://opds-spec.org/image/thumbnail"'
    ' href="https://www.gutenberg.org/cache/epub/1727/pg1727.cover.small.jpg"/>'
    "</entry></feed>"
)
SUBJECTS_FEED = _HEAD + "<entry><title>Greek epic</title></entry></feed>"

ROOT_WITH_BOTH_SEARCH_LINKS = (
    _HEAD + '<link href="/opds-opensearch.xml" rel="search"'
    ' type="application/opensearchdescription+xml"/>'
    '<link href="/opds/search?searchTerm={searchTerms}" rel="search"'
    ' type="application/atom+xml"/></feed>'
)
OPENSEARCH_DESCRIPTION = (
    '<?xml version="1.0"?><OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
    '<Url type="application/atom+xml" template="http://flibusta.is/opds/opensearch'
    '?searchTerm={searchTerms}&amp;searchType=books&amp;pageNumber={startPage?}"/>'
    "</OpenSearchDescription>"
)


def _config(values: dict) -> MagicMock:
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None, **_kw: values.get(key, default)
    return mock


def _feed(url: str, auth: tuple[str, str] | None = None) -> opds.Feed:
    return opds.Feed(url=url, auth=auth, origin=opds.url_origin(url), label=opds._feed_label(url))


def _plan(manual_query: str) -> ReleaseSearchPlan:
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="",
        title_variants=[],
        grouped_title_variants=[],
        manual_query=manual_query,
    )


def _book() -> BookMetadata:
    return BookMetadata(provider="manual", provider_id="1", title="Test")


def _serve(pages: dict[str, str]):
    """Return a fake ``_FeedReader.get`` that serves documents by URL substring."""

    def get(_reader, url: str) -> bytes:
        for marker, body in pages.items():
            if marker in url:
                return body.encode()
        raise opds._FeedError(f"unexpected {url}")

    return get


@pytest.fixture(autouse=True)
def _clear_caches():
    opds._template_cache.clear()
    opds._book_feed_cache.clear()
    yield
    opds._template_cache.clear()
    opds._book_feed_cache.clear()


class TestRegistration:
    def test_package_import_registers_source_and_handler(self):
        from shelfmark.release_sources import get_handler, get_source

        assert isinstance(get_source("opds"), OPDSSource)
        assert isinstance(get_handler("opds"), OPDSHandler)


class TestConfiguredFeeds:
    def test_credentials_leave_the_url_and_become_a_login(self):
        values = {"OPDS_FEEDS": ["http://reader:p%40ss@calibre.lan:8083/opds"]}
        with patch(f"{_SOURCE}.config", _config(values)):
            (feed,) = opds.configured_feeds()
        assert feed.url == "http://calibre.lan:8083/opds"
        assert feed.auth == ("reader", "p@ss")
        assert feed.origin == "http://calibre.lan:8083"

    def test_comma_separated_env_text_and_junk(self):
        values = {
            "OPDS_FEEDS": "https://a.example/opds, not-a-url ,https://b.example/s?q={searchTerms}"
        }
        with patch(f"{_SOURCE}.config", _config(values)):
            feeds = opds.configured_feeds()
        assert [feed.label for feed in feeds] == ["a.example", "b.example"]

    def test_default_is_gutenberg(self):
        with patch(f"{_SOURCE}.config", _config({})):
            assert [feed.label for feed in opds.configured_feeds()] == ["gutenberg.org"]


class TestParsing:
    def test_flibusta_entry_maps_zip_mime_types_and_skips_unknown(self):
        feed = _feed("https://flibusta.is/opds/opensearch?searchTerm={searchTerms}")
        with patch.object(opds._FeedReader, "get", _serve({"opensearch": FLIBUSTA_SEARCH})):
            (entry,) = opds.search_feed(opds._FeedReader(feed, 5), "дюна", 1, 25)
        release = opds._to_release(entry, feed, _WANTED)

        assert release is not None
        assert release.format == "epub"
        assert release.source_id == "https://flibusta.is/b/7/epub"
        assert release.extra["format_variants"] == {
            "epub": "https://flibusta.is/b/7/epub",
            "fb2": "https://flibusta.is/b/7/fb2",
        }
        assert release.language == "ru"
        assert release.extra["year"] == "1965"
        assert release.extra["author"] == "Герберт Фрэнк"
        assert release.info_url == "https://flibusta.is/b/7"
        assert release.indexer == "flibusta.is"

    def test_gutenberg_books_are_read_from_their_own_feeds(self):
        feed = _feed("https://www.gutenberg.org/ebooks/search.opds/?query={searchTerms}")
        pages = {
            "search.opds/?query=odyssey": GUTENBERG_SEARCH,
            "subjects/search.opds": SUBJECTS_FEED,
            "/ebooks/1727.opds": GUTENBERG_BOOK,
        }
        with patch.object(opds._FeedReader, "get", _serve(pages)):
            entries = opds.search_feed(opds._FeedReader(feed, 5), "odyssey", 1, 25)
        (release,) = [opds._to_release(entry, feed, _WANTED) for entry in entries]

        # The navigation entry ("Subjects") has no files and is dropped.
        assert release.title == "The Odyssey"
        # Files of both entries of the book feed are merged; images beat "no images".
        assert release.source_id == "https://www.gutenberg.org/ebooks/1727.epub3.images"
        assert release.size == "435 KB"
        assert release.extra["format_variants"]["mobi"].endswith("1727.kf8.images")
        assert release.extra["preview"].endswith("pg1727.cover.small.jpg")
        assert release.extra["author"] == "Homer"

    def test_entry_without_a_wanted_format_gives_no_release(self):
        feed = _feed("https://flibusta.is/opds/opensearch?searchTerm={searchTerms}")
        with patch.object(opds._FeedReader, "get", _serve({"opensearch": FLIBUSTA_SEARCH})):
            (entry,) = opds.search_feed(opds._FeedReader(feed, 5), "x", 1, 25)
        assert opds._to_release(entry, feed, ["pdf"]) is None

    def test_later_page_follows_next_links(self):
        feed = _feed("https://flibusta.is/opds/opensearch?searchTerm={searchTerms}")
        second = FLIBUSTA_SEARCH.replace("Дюна", "Дюна 2").replace(
            '<link href="/opds/opensearch?searchTerm=x&amp;pageNumber=1" rel="next"', "<link"
        )
        pages = {"pageNumber=1": second, "opensearch": FLIBUSTA_SEARCH}
        with patch.object(opds._FeedReader, "get", _serve(pages)):
            reader = opds._FeedReader(feed, 5)
            (entry,) = opds.search_feed(reader, "x", 2, 25)
            assert entry.title == "Дюна 2"
            assert opds.search_feed(reader, "x", 3, 25) == []


class TestTemplates:
    def test_root_prefers_the_opensearch_description_and_stays_on_https(self):
        feed = _feed("https://flibusta.is/opds")
        pages = {
            "opds-opensearch.xml": OPENSEARCH_DESCRIPTION,
            "/opds": ROOT_WITH_BOTH_SEARCH_LINKS,
        }
        with patch.object(opds._FeedReader, "get", _serve(pages)):
            template = opds._search_template(opds._FeedReader(feed, 5))
        assert template == (
            "https://flibusta.is/opds/opensearch?searchTerm={searchTerms}"
            "&searchType=books&pageNumber={startPage?}"
        )

    def test_root_without_a_description_uses_its_atom_search_link(self):
        feed = _feed("https://calibre.example/opds")
        root = ROOT_WITH_BOTH_SEARCH_LINKS.replace("opensearchdescription+xml", "text/html")
        with patch.object(opds._FeedReader, "get", _serve({"/opds": root})):
            template = opds._search_template(opds._FeedReader(feed, 5))
        assert template == "https://calibre.example/opds/search?searchTerm={searchTerms}"

    def test_root_without_search_is_an_error(self):
        feed = _feed("https://calibre.example/opds")
        with (
            patch.object(opds._FeedReader, "get", _serve({"/opds": _HEAD + "</feed>"})),
            pytest.raises(opds._FeedError, match="no search link"),
        ):
            opds._search_template(opds._FeedReader(feed, 5))

    def test_fill_template_leaves_optional_values_empty(self):
        filled = opds._fill_template(
            "https://x/s?q={searchTerms}&p={startPage?}&n={count}&i={startIndex}", "a b", 25
        )
        assert filled == "https://x/s?q=a%20b&p=&n=25&i=1"


class TestReader:
    def test_login_goes_only_to_the_feeds_own_origin(self):
        feed = _feed("http://calibre.lan:8083/opds", auth=("u", "p"))
        reader = opds._FeedReader(feed, 5)
        response = MagicMock(content=b"<feed/>")
        with (
            patch(f"{_SOURCE}.requests.get", return_value=response) as get,
            patch(f"{_SOURCE}.prepare_public_url", side_effect=lambda url: url) as public_check,
        ):
            reader.get("http://calibre.lan:8083/opds/search?q=x")
            assert get.call_args.kwargs["auth"] == ("u", "p")
            public_check.assert_not_called()

            reader.get("https://cdn.example.com/feed.xml")
            assert get.call_args.kwargs["auth"] is None
            public_check.assert_called_once()

    def test_a_private_link_off_the_feed_origin_is_refused(self):
        reader = opds._FeedReader(_feed("https://feeds.example/opds"), 5)
        with (
            patch(f"{_SOURCE}.prepare_public_url", return_value=None),
            pytest.raises(opds._FeedError, match="non-public"),
        ):
            reader.get("http://192.168.1.1/admin")


class TestSearch:
    def _search(self, values: dict, get) -> list:
        settings = {"OPDS_ENABLED": True, **values}
        with (
            patch(f"{_SOURCE}.config", _config(settings)),
            patch(_SUPPORTED_FORMATS, return_value=_WANTED),
            patch.object(opds._FeedReader, "get", get),
        ):
            return OPDSSource().search(_book(), _plan("дюна"))

    def test_feeds_are_merged(self):
        values = {
            "OPDS_FEEDS": [
                "https://flibusta.is/opds/opensearch?searchTerm={searchTerms}",
                "https://other.example/opensearch?searchTerm={searchTerms}",
            ]
        }
        releases = self._search(values, _serve({"opensearch": FLIBUSTA_SEARCH}))
        assert [release.indexer for release in releases] == ["flibusta.is", "other.example"]

    def test_every_feed_failing_reports_the_source_unavailable(self):
        values = {"OPDS_FEEDS": ["https://a.example/s?q={searchTerms}"]}
        with pytest.raises(SourceUnavailableError, match="a.example"):
            self._search(values, _serve({}))

    def test_one_failing_feed_does_not_hide_the_others(self):
        values = {
            "OPDS_FEEDS": [
                "https://dead.example/s?q={searchTerms}",
                "https://flibusta.is/opds/opensearch?searchTerm={searchTerms}",
            ]
        }
        releases = self._search(values, _serve({"flibusta.is": FLIBUSTA_SEARCH}))
        assert len(releases) == 1

    def test_disabled_source_does_nothing(self):
        with patch(f"{_SOURCE}.config", _config({"OPDS_ENABLED": False})):
            assert OPDSSource().search(_book(), _plan("x")) == []


class TestHandler:
    def test_downloads_the_id_url_with_the_feed_logins(self):
        task = SimpleNamespace(
            task_id="http://calibre.lan:8083/get/epub/1", source_url="ignored", format="epub"
        )
        values = {"OPDS_FEEDS": ["http://u:p@calibre.lan:8083/opds"]}
        with (
            patch(f"{_SOURCE}.config", _config(values)),
            patch(f"{_HANDLER}.download_public_file", return_value="/tmp/x.epub") as download,
        ):
            result = OPDSHandler().download(
                task, threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result == "/tmp/x.epub"
        assert download.call_args.args[0] == "http://calibre.lan:8083/get/epub/1"
        assert download.call_args.kwargs["fallback_format"] == "epub"
        assert download.call_args.kwargs["trusted_origins"] == {
            "http://calibre.lan:8083": ("u", "p")
        }

    def test_refuses_an_id_that_is_not_a_url(self):
        statuses = []
        task = SimpleNamespace(task_id="javascript:alert(1)", source_url=None)
        result = OPDSHandler().download(
            task, threading.Event(), lambda _p: None, lambda s, m: statuses.append((s, m))
        )
        assert result is None
        assert statuses == [("error", "Not an OPDS file URL")]


class TestRelevance:
    def test_entries_without_a_query_word_are_dropped(self):
        noise = FLIBUSTA_SEARCH.replace("Дюна", "Книга о слонах").replace("Герберт Фрэнк", "Автор")
        values = {"OPDS_FEEDS": ["https://flibusta.is/opds/opensearch?searchTerm={searchTerms}"]}
        settings = {"OPDS_ENABLED": True, **values}
        with (
            patch(f"{_SOURCE}.config", _config(settings)),
            patch(_SUPPORTED_FORMATS, return_value=_WANTED),
            patch.object(opds._FeedReader, "get", _serve({"opensearch": noise})),
        ):
            assert OPDSSource().search(_book(), _plan("Οδύσσεια")) == []

    def test_a_word_in_the_author_counts(self):
        entry = opds._Entry(title="Собрание сочинений", authors=["Frank Herbert"])
        assert opds._matches(entry, {"herbert"})

    def test_accents_and_case_do_not_matter(self):
        entry = opds._Entry(title="ΟΔΥΣΣΕΙΑ")
        assert opds._matches(entry, {"οδυσσεια"})
