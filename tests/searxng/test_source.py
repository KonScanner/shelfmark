"""Tests for the SearXNG release source."""

from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.searxng.source import (
    SearXNGSource,
    _build_requests,
    _direct_file_url,
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

    def test_wikimedia_file_page_becomes_file_path(self):
        assert (
            _direct_file_url("https://commons.wikimedia.org/wiki/File:Mikron_Euchologion.pdf")
            == "https://commons.wikimedia.org/wiki/Special:FilePath/Mikron_Euchologion.pdf"
        )

    def test_github_blob_becomes_raw(self):
        assert (
            _direct_file_url("https://github.com/owner/books/blob/master/books/Dune.epub")
            == "https://github.com/owner/books/raw/master/books/Dune.epub"
        )

    def test_other_urls_unchanged(self):
        url = "https://example.com/files/book.pdf"
        assert _direct_file_url(url) == url


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

    def test_one_query_fans_out_into_file_queries(self):
        with patch(
            "shelfmark.release_sources.searxng.source.config", _config_map(self._enabled_config())
        ):
            params = _build_requests(["Ο Σωτήρ"])

        assert [p["q"] for p in params] == [
            "Ο Σωτήρ",
            "Ο Σωτήρ filetype:pdf",
            "Ο Σωτήρ ext:epub",
            "Ο Σωτήρ",
        ]
        assert all(p["categories"] == "general" for p in params[:3])
        # The file-engine pass names its engines and drops the category filter.
        assert params[3]["engines"] == "findfiles,wikicommons.files"
        assert "categories" not in params[3]

    def test_only_the_first_query_goes_to_the_general_engines(self):
        with patch(
            "shelfmark.release_sources.searxng.source.config", _config_map(self._enabled_config())
        ):
            params = _build_requests(["Dune Herbert", "Dune"])

        assert [(p["q"], "categories" in p) for p in params] == [
            ("Dune Herbert", True),
            ("Dune Herbert filetype:pdf", True),
            ("Dune Herbert ext:epub", True),
            ("Dune Herbert", False),
            ("Dune", False),
        ]

    def test_every_request_limits_the_engine_wait(self):
        with patch(
            "shelfmark.release_sources.searxng.source.config", _config_map(self._enabled_config())
        ):
            params = _build_requests(["Dune Herbert", "Dune"], page=2)

        assert all(p["timeout_limit"] == "5" for p in params)

    def test_later_page_asks_every_request_for_that_page(self):
        with patch(
            "shelfmark.release_sources.searxng.source.config", _config_map(self._enabled_config())
        ):
            first = _build_requests(["q"])
            third = _build_requests(["q"], page=3)

        assert all("pageno" not in params for params in first)
        assert all(params["pageno"] == "3" for params in third)

    def test_empty_file_engines_skip_that_pass(self):
        values = {**self._enabled_config(), "SEARXNG_FILE_ENGINES": ""}
        with patch("shelfmark.release_sources.searxng.source.config", _config_map(values)):
            params = _build_requests(["q"])

        assert [p["q"] for p in params] == ["q", "q filetype:pdf", "q ext:epub"]

    def test_results_from_all_requests_are_merged(self):
        def _get(_url, params, timeout):
            response = MagicMock()
            response.status_code = 200
            url = f"https://example.com/{params['q'].replace(' ', '_').replace(':', '-')}.pdf"
            if params.get("engines"):
                url = "https://example.com/from_file_engine.pdf"
            response.json.return_value = {"results": [{"url": url, "title": params["q"]}]}
            return response

        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", side_effect=_get),
        ):
            releases = SearXNGSource().search(_book(), _plan(manual_query="dune"))

        assert sorted(release.download_url for release in releases) == [
            "https://example.com/dune.pdf",
            "https://example.com/dune_ext-epub.pdf",
            "https://example.com/dune_filetype-pdf.pdf",
            "https://example.com/from_file_engine.pdf",
        ]

    def test_one_failed_request_keeps_the_others(self):
        import requests as requests_module

        def _get(_url, params, timeout):
            if "filetype:pdf" in params["q"]:
                raise requests_module.ConnectionError("engine down")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "results": [{"url": "https://example.com/a.pdf", "title": "A"}]
            }
            return response

        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", side_effect=_get),
        ):
            releases = SearXNGSource().search(_book(), _plan(manual_query="q"))

        assert [release.download_url for release in releases] == ["https://example.com/a.pdf"]

    def test_www_and_scheme_variants_are_one_link(self):
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {
            "results": [
                {"url": "https://www.example.com/a.pdf", "title": "First"},
                {"url": "http://example.com/a.pdf", "title": "Same file"},
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

    def test_links_naming_the_query_come_first(self):
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {
            "results": [
                {"url": "https://example.com/sand.pdf", "title": "Coastal sand dune geology"},
                {"url": "https://example.com/x.pdf", "title": "Frank Herbert - Dune"},
                {"url": "https://example.com/y.pdf", "title": "Ο ΣΩΤΗΡ"},
            ]
        }
        with (
            patch(
                "shelfmark.release_sources.searxng.source.config",
                _config_map(self._enabled_config()),
            ),
            patch("shelfmark.release_sources.searxng.source.requests.get", return_value=payload),
        ):
            dune = SearXNGSource().search(_book(), _plan(manual_query="Dune Frank Herbert"))
            greek = SearXNGSource().search(_book(), _plan(manual_query="Ο Σωτήρ"))

        assert dune[0].title == "Frank Herbert - Dune"
        # Case and accents are ignored: "Σωτήρ" matches "ΣΩΤΗΡ".
        assert greek[0].title == "Ο ΣΩΤΗΡ"

    def test_wikimedia_title_loses_file_prefix(self):
        payload = MagicMock()
        payload.status_code = 200
        payload.json.return_value = {
            "results": [
                {
                    "url": "https://commons.wikimedia.org/wiki/File:Mikron_Euchologion.pdf",
                    "title": "File:Mikron Euchologion.pdf",
                }
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

        assert releases[0].title == "Mikron Euchologion.pdf"
        assert releases[0].download_url.endswith("/wiki/Special:FilePath/Mikron_Euchologion.pdf")

    def test_request_error_is_reported(self):
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
            with pytest.raises(SourceUnavailableError, match="did not answer any"):
                SearXNGSource().search(_book(), _plan(manual_query="q"))


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
