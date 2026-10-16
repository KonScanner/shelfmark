"""Tests for the Internet Archive release source and its download handler."""

from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.internet_archive.handler import InternetArchiveHandler, pick_file
from shelfmark.release_sources.internet_archive.source import (
    InternetArchiveSource,
    build_query,
    split_source_id,
)

_SOURCE = "shelfmark.release_sources.internet_archive.source"
_HANDLER = "shelfmark.release_sources.internet_archive.handler"
_FORMATS = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr", "pdf"]


def _config(values: dict) -> MagicMock:
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None, **_kw: values.get(key, default)
    return mock


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


def _search_response(docs: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"response": {"numFound": len(docs), "docs": docs}}
    return response


class TestRegistration:
    def test_package_import_registers_source_and_handler(self):
        from shelfmark.release_sources import get_handler, get_source

        assert isinstance(get_source("internet_archive"), InternetArchiveSource)
        assert isinstance(get_handler("internet_archive"), InternetArchiveHandler)

    def test_results_are_releases(self):
        assert InternetArchiveSource().search_results_are_releases() is True


class TestBuildQuery:
    def test_accented_words_also_search_without_accents(self):
        assert build_query("Ο Σωτήρ") == (
            "((Ο Σωτήρ) OR (Ο Σωτηρ)) AND mediatype:texts AND NOT access-restricted-item:true"
        )

    def test_plain_words_are_not_repeated(self):
        assert build_query("Dune Frank Herbert") == (
            "((Dune Frank Herbert)) AND mediatype:texts AND NOT access-restricted-item:true"
        )

    def test_query_syntax_is_removed(self):
        assert build_query('Dune: "Messiah" (1969)') == (
            "((Dune Messiah 1969)) AND mediatype:texts AND NOT access-restricted-item:true"
        )

    def test_empty_text_has_no_query(self):
        assert build_query(' ()" ') is None


class TestSearch:
    def test_one_release_per_item_in_the_first_wanted_format(self):
        docs = [
            {
                "identifier": "dune-frank-herbert",
                "title": "Dune",
                "creator": ["Frank Herbert"],
                "year": 1965,
                "language": ["eng"],
                "format": ["Text PDF", "EPUB", "DjVuTXT", "LCP Encrypted EPUB"],
                "downloads": 4200,
            },
            # Only encrypted lending copies: no release.
            {"identifier": "lending-copy", "title": "Dune", "format": ["ACS Encrypted PDF"]},
        ]
        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.get_supported_formats", return_value=_FORMATS),
            patch(f"{_SOURCE}.requests.get", return_value=_search_response(docs)) as fake_get,
        ):
            releases = InternetArchiveSource().search(_book(), _plan("Dune Frank Herbert"))

        assert len(releases) == 1
        release = releases[0]
        assert (release.source_id, release.format) == ("dune-frank-herbert:epub", "epub")
        assert release.extra["format_variants"] == {
            "epub": "dune-frank-herbert:epub",
            "pdf": "dune-frank-herbert:pdf",
        }
        assert release.language == "en"
        assert release.info_url == "https://archive.org/details/dune-frank-herbert"
        assert release.extra["author"] == "Frank Herbert"
        assert release.extra["year"] == "1965"
        assert release.extra["downloads"] == 4200
        params = dict(fake_get.call_args.kwargs["params"])
        assert "NOT access-restricted-item:true" in params["q"]

    def test_browse_format_filter_picks_the_format(self):
        from shelfmark.core.models import SearchFilters

        docs = [{"identifier": "x", "title": "X", "format": ["EPUB", "Text PDF"]}]
        plan = replace(_plan("x"), source_filters=SearchFilters(format=["pdf"]))
        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.get_supported_formats", return_value=_FORMATS),
            patch(f"{_SOURCE}.requests.get", return_value=_search_response(docs)),
        ):
            releases = InternetArchiveSource().search(_book(), plan)

        assert [(r.source_id, r.format) for r in releases] == [("x:pdf", "pdf")]

    def test_later_page_is_requested(self):
        from shelfmark.core.models import SearchFilters

        plan = replace(_plan("x"), source_filters=SearchFilters(page=2))
        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.requests.get", return_value=_search_response([])) as fake_get,
        ):
            InternetArchiveSource().search(_book(), plan)

        assert dict(fake_get.call_args.kwargs["params"])["page"] == "2"

    def test_unwanted_formats_are_skipped(self):
        docs = [{"identifier": "x", "title": "X", "format": ["EPUB", "Text PDF"]}]
        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.get_supported_formats", return_value=["pdf"]),
            patch(f"{_SOURCE}.requests.get", return_value=_search_response(docs)),
        ):
            releases = InternetArchiveSource().search(_book(), _plan("x"))

        assert [r.format for r in releases] == ["pdf"]
        assert releases[0].extra["format_variants"] == {"pdf": "x:pdf"}

    def test_disabled_source_does_not_search(self):
        with (
            patch(f"{_SOURCE}.config", _config({})),
            patch(f"{_SOURCE}.requests.get") as fake_get,
        ):
            assert InternetArchiveSource().search(_book(), _plan("x")) == []
        fake_get.assert_not_called()

    def test_unreachable_archive_is_reported(self):
        import requests

        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.requests.get", side_effect=requests.ConnectionError("down")),
            pytest.raises(SourceUnavailableError, match="Internet Archive search failed"),
        ):
            InternetArchiveSource().search(_book(), _plan("x"))

    def test_search_uses_the_configured_proxy(self):
        proxies = {"https": "socks5://proxy:1080"}
        with (
            patch(f"{_SOURCE}.config", _config({"INTERNET_ARCHIVE_ENABLED": True})),
            patch(f"{_SOURCE}.get_proxies", return_value=proxies),
            patch(f"{_SOURCE}.requests.get", return_value=_search_response([])) as fake_get,
        ):
            InternetArchiveSource().search(_book(), _plan("x"))

        assert fake_get.call_args.kwargs["proxies"] == proxies


class TestRecordIds:
    def test_split_source_id(self):
        assert split_source_id("soter-1925-2:pdf") == ("soter-1925-2", "pdf")

    def test_split_source_id_rejects_unknown_format(self):
        assert split_source_id("soter-1925-2:txt") is None
        assert split_source_id("no-format") is None

    @pytest.mark.parametrize(
        "source_id",
        ["../../etc:pdf", "a/b:pdf", "item?x=1:pdf", ":pdf", "a b:pdf"],
        ids=["dot-dot", "slash", "query", "empty", "space"],
    )
    def test_split_source_id_rejects_unsafe_identifiers(self, source_id):
        assert split_source_id(source_id) is None

    def test_get_record_rebuilds_from_id(self):
        record = InternetArchiveSource().get_record("soter-1925-2:pdf")

        assert record is not None
        assert record.format == "pdf"
        assert record.source_url == "https://archive.org/details/soter-1925-2"


class TestPickFile:
    FILES = [
        {"name": "book_encrypted.pdf", "format": "ACS Encrypted PDF"},
        {"name": "book_jpg.pdf", "format": "Image Container PDF", "source": "derivative"},
        {"name": "book.pdf", "format": "Text PDF", "source": "derivative"},
        {"name": "book.epub", "format": "EPUB", "source": "derivative", "private": "true"},
        {"name": "Book Original.epub", "format": "EPUB", "source": "original"},
    ]

    def test_preferred_pdf_name_wins(self):
        assert pick_file(self.FILES, "pdf") == "book.pdf"

    def test_private_files_are_skipped(self):
        assert pick_file(self.FILES, "epub") == "Book Original.epub"

    def test_missing_format(self):
        assert pick_file(self.FILES, "djvu") is None


class TestHandler:
    def test_downloads_the_picked_file(self):
        listing = MagicMock()
        listing.json.return_value = {
            "result": [{"name": "Dune (Frank Herbert).epub", "format": "EPUB"}]
        }
        task = SimpleNamespace(task_id="dune-frank-herbert:epub", source_url=None)
        with (
            patch(f"{_HANDLER}.requests.get", return_value=listing),
            patch(f"{_HANDLER}.download_public_file", return_value="/tmp/x.epub") as fake_download,
        ):
            path = InternetArchiveHandler().download(
                task, threading.Event(), lambda _p: None, lambda _s, _m: None
            )

        assert path == "/tmp/x.epub"
        assert fake_download.call_args.args[0] == (
            "https://archive.org/download/dune-frank-herbert/Dune%20%28Frank%20Herbert%29.epub"
        )

    def test_reports_an_item_without_the_format(self):
        listing = MagicMock()
        listing.json.return_value = {"result": [{"name": "a.pdf", "format": "Text PDF"}]}
        task = SimpleNamespace(task_id="item:epub", source_url=None)
        statuses: list[tuple[str, str | None]] = []
        with patch(f"{_HANDLER}.requests.get", return_value=listing):
            path = InternetArchiveHandler().download(
                task, threading.Event(), lambda _p: None, lambda s, m: statuses.append((s, m))
            )

        assert path is None
        assert statuses[-1] == ("error", "The item has no free EPUB file")
