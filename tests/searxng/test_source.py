"""Tests for the SearXNG release source."""

from unittest.mock import MagicMock, patch

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.searxng.source import (
    SearXNGSource,
    _filename_from_url,
    _url_extension,
)


class TestRegistration:
    """Import-time registration smoke tests."""

    def test_package_import_registers_source_and_handler(self):
        import shelfmark.release_sources.searxng  # noqa: F401
        from shelfmark.release_sources import get_handler, get_source

        assert get_source("searxng") is not None
        assert get_handler("searxng") is not None


def _plan(manual_query: str | None = None, title: str = "", author: str = "") -> ReleaseSearchPlan:
    """Build a minimal search plan for tests."""
    variants = [ReleaseSearchVariant(title=title, author=author)] if title else []
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author=author,
        title_variants=variants,
        grouped_title_variants=variants,
        manual_query=manual_query,
    )


def _book(title: str = "Test Book") -> BookMetadata:
    return BookMetadata(provider="manual", provider_id="1", title=title)


def _config_map(values: dict) -> MagicMock:
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None: values.get(key, default)
    return mock


class TestUrlHelpers:
    """Tests for URL extension and filename helpers."""

    def test_url_extension_plain(self):
        assert _url_extension("https://example.com/files/book.pdf") == "pdf"

    def test_url_extension_with_query(self):
        assert _url_extension("https://example.com/b.epub?download=1") == "epub"

    def test_url_extension_encoded(self):
        assert _url_extension("https://example.com/%CE%95%CE%BB.mobi") == "mobi"

    def test_url_extension_missing(self):
        assert _url_extension("https://example.com/page") is None

    def test_filename_from_url(self):
        assert _filename_from_url("https://example.com/dir/My_Book.pdf") == "My_Book"


class TestAvailability:
    """Tests for is_available."""

    def test_disabled_source_unavailable(self):
        with patch("shelfmark.release_sources.searxng.source.config", _config_map({})):
            assert SearXNGSource().is_available() is False

    def test_enabled_with_url_available(self):
        values = {"SEARXNG_ENABLED": True, "SEARXNG_BASE_URL": "http://searxng:8080"}
        with patch("shelfmark.release_sources.searxng.source.config", _config_map(values)):
            assert SearXNGSource().is_available() is True


class TestSearch:
    """Tests for the search flow."""

    def _enabled_config(self) -> dict:
        return {
            "SEARXNG_ENABLED": True,
            "SEARXNG_BASE_URL": "http://searxng:8080",
            "SEARXNG_CATEGORIES": "general",
            "SEARXNG_ENGINES": "",
            "SEARXNG_LANGUAGE": "",
            "SEARXNG_TIMEOUT": 5,
            "SEARXNG_MAX_RESULTS": 10,
        }

    def test_audiobook_content_type_skipped(self):
        source = SearXNGSource()
        with patch(
            "shelfmark.release_sources.searxng.source.config", _config_map(self._enabled_config())
        ):
            assert source.search(_book(), _plan(), content_type="audiobook") == []

    def test_only_file_links_become_releases(self):
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {
            "results": [
                {"url": "https://example.com/a.pdf", "title": "A Pdf", "engines": ["bing"]},
                {"url": "https://example.com/page", "title": "A Web Page"},
                {"url": "https://example.com/b.epub", "title": "B Epub", "engine": "google"},
            ]
        }
        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", return_value=payload),
        ):
            releases = SearXNGSource().search(
                _book(), _plan(manual_query="test query"), content_type="ebook"
            )

        assert [release.format for release in releases] == ["pdf", "epub"]
        assert releases[0].download_url == "https://example.com/a.pdf"
        assert releases[0].extra["engine"] == "bing"
        assert releases[1].extra["engine"] == "google"

    def test_duplicate_urls_removed(self):
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {
            "results": [
                {"url": "https://example.com/a.pdf", "title": "First"},
                {"url": "https://example.com/a.pdf", "title": "Duplicate"},
            ]
        }
        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", return_value=payload),
        ):
            releases = SearXNGSource().search(_book(), _plan(manual_query="q"))

        assert len(releases) == 1

    def test_max_results_respected(self):
        results = [{"url": f"https://example.com/{i}.pdf", "title": f"Book {i}"} for i in range(20)]
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {"results": results}
        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", return_value=payload),
        ):
            releases = SearXNGSource().search(_book(), _plan(manual_query="q"))

        assert len(releases) == 10

    def test_403_returns_no_releases(self):
        payload = MagicMock()
        payload.status_code = 403
        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", return_value=payload),
        ):
            assert SearXNGSource().search(_book(), _plan(manual_query="q")) == []

    def test_request_error_returns_no_releases(self):
        import requests as requests_module

        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch(
                "shelfmark.release_sources.searxng.source.requests.get",
                side_effect=requests_module.ConnectionError("down"),
            ),
        ):
            assert SearXNGSource().search(_book(), _plan(manual_query="q")) == []


class TestBrowseShape:
    """Tests for the source's browse-facing surface."""

    def test_results_are_releases(self):
        assert SearXNGSource().search_results_are_releases() is True

    def test_supported_content_types_ebook_only(self):
        assert SearXNGSource().supported_content_types == ["ebook"]

    def test_get_record_rebuilds_from_url(self):
        with patch("shelfmark.release_sources.searxng.source.config", _config_map({})):
            record = SearXNGSource().get_record("https://example.com/dir/My_Book.pdf")

        assert record is not None
        assert record.title == "My_Book"
        assert record.format == "pdf"
        assert record.download_urls == ["https://example.com/dir/My_Book.pdf"]

    def test_get_record_rejects_page_url(self):
        with patch("shelfmark.release_sources.searxng.source.config", _config_map({})):
            assert SearXNGSource().get_record("https://example.com/page") is None

    def test_get_record_rejects_non_url(self):
        with patch("shelfmark.release_sources.searxng.source.config", _config_map({})):
            assert SearXNGSource().get_record("not-a-url") is None
