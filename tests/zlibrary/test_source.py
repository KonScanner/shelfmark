"""Tests for the Z-Library release source and its download handler."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.zlibrary import source as zlib
from shelfmark.release_sources.zlibrary.handler import (
    NO_ACCOUNT_MESSAGE,
    ZLibraryHandler,
    resolve_download_link,
)
from shelfmark.release_sources.zlibrary.source import ZLibrarySource

_SOURCE = "shelfmark.release_sources.zlibrary.source"
_HANDLER = "shelfmark.release_sources.zlibrary.handler"
_SUPPORTED_FORMATS = "shelfmark.release_sources.direct_download.common.get_supported_formats"
_MIRRORS = ["https://z-lib.gd", "https://z-lib.gl"]

BOOK = {
    "id": 3488801,
    "hash": "7faa5e",
    "title": "Πηγαίνεις στην Εκκλησία ;",
    "author": "Συλλογικό έργο",
    "year": 1995,
    "publisher": "Ο Σωτήρ",
    "language": "Greek",
    "extension": "PDF",
    "filesizeString": "636 KB",
    "md5": "48BD87C79038A0469FB13DC80FE4A7AD",
    "cover": "https://covers.example/1.jpg",
    "href": "https://z-lib.gd/book/eNrY5jZ7r7/x.html",
}


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


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


@pytest.fixture(autouse=True)
def _no_cached_sessions():
    zlib._sessions.clear()
    yield
    zlib._sessions.clear()


class TestRegistration:
    def test_package_import_registers_source_and_handler(self):
        from shelfmark.release_sources import get_handler, get_source

        assert isinstance(get_source("zlibrary"), ZLibrarySource)
        assert isinstance(get_handler("zlibrary"), ZLibraryHandler)


class TestSourceIds:
    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("zlibrary:3488801:7faa5e", ("3488801", "7faa5e")),
            ("zlibrary:abc:7faa5e", None),
            ("zlibrary:1:../../x", None),
            ("libgen:1:abc", None),
        ],
    )
    def test_split_source_id(self, source_id, expected):
        assert zlib.split_source_id(source_id) == expected


class TestRelease:
    def test_book_maps_to_a_release(self):
        release = zlib._book_to_release(BOOK)
        assert release is not None
        assert release.source_id == "zlibrary:3488801:7faa5e"
        assert release.format == "pdf"
        assert release.language == "el"
        assert release.size == "636 KB"
        assert release.download_url is None
        assert release.extra["md5"] == "48bd87c79038a0469fb13dc80fe4a7ad"
        assert release.extra["year"] == "1995"
        assert release.info_url == BOOK["href"]

    def test_book_without_hash_is_skipped(self):
        assert zlib._book_to_release({**BOOK, "hash": ""}) is None


class TestSearch:
    def _search(self, responses, values=None):
        settings = {"ZLIBRARY_SEARCH_ENABLED": True, **(values or {})}
        with (
            patch(f"{_SOURCE}.config", _config(settings)),
            patch(f"{_SOURCE}.configured_mirrors", return_value=_MIRRORS),
            patch(_SUPPORTED_FORMATS, return_value=["epub", "pdf"]),
            patch(f"{_SOURCE}.requests.request", side_effect=responses) as request,
        ):
            return ZLibrarySource().search(_book(), _plan("Σωτήρ")), request

    def test_search_sends_the_query_page_and_formats(self):
        releases, request = self._search([_response({"success": 1, "books": [BOOK]})])
        assert [release.title for release in releases] == [BOOK["title"]]
        method, url = request.call_args.args
        assert (method, url) == ("POST", "https://z-lib.gd/eapi/book/search")
        assert request.call_args.kwargs["data"] == {
            "message": "Σωτήρ",
            "limit": 50,
            "page": 1,
            "extensions[]": ["epub", "pdf"],
        }

    def test_a_dead_mirror_falls_through_to_the_next(self):
        import requests

        releases, request = self._search(
            [requests.ConnectionError("down"), _response({"success": 1, "books": [BOOK]})]
        )
        assert len(releases) == 1
        assert request.call_args.args[1] == "https://z-lib.gl/eapi/book/search"

    def test_no_mirror_answering_reports_the_source_unavailable(self):
        import requests

        with pytest.raises(SourceUnavailableError, match="No Z-Library mirror answered"):
            self._search([requests.ConnectionError("a"), requests.ConnectionError("b")])

    def test_unavailable_without_mirrors(self):
        with (
            patch(f"{_SOURCE}.config", _config({"ZLIBRARY_SEARCH_ENABLED": True})),
            patch(f"{_SOURCE}.configured_mirrors", return_value=[]),
        ):
            assert ZLibrarySource().is_available() is False


class TestAccount:
    def test_session_cookies_win_over_a_login(self):
        values = {
            "ZLIBRARY_REMIX_USERID": "42",
            "ZLIBRARY_REMIX_USERKEY": "k",
            "ZLIBRARY_EMAIL": "a@b.c",
            "ZLIBRARY_PASSWORD": "pw",
        }
        with (
            patch(f"{_SOURCE}.config", _config(values)),
            patch(f"{_SOURCE}.requests.request") as request,
        ):
            assert zlib.session_cookies(_MIRRORS[0]) == {
                "remix_userid": "42",
                "remix_userkey": "k",
            }
        request.assert_not_called()

    def test_login_is_cached_per_mirror(self):
        values = {"ZLIBRARY_EMAIL": "a@b.c", "ZLIBRARY_PASSWORD": "pw"}
        login = _response({"success": 1, "user": {"id": 42, "remix_userkey": "k"}})
        with (
            patch(f"{_SOURCE}.config", _config(values)),
            patch(f"{_SOURCE}.requests.request", return_value=login) as request,
        ):
            first = zlib.session_cookies(_MIRRORS[0])
            second = zlib.session_cookies(_MIRRORS[0])
        assert first == second == {"remix_userid": "42", "remix_userkey": "k"}
        assert request.call_count == 1
        assert request.call_args.kwargs["data"] == {"email": "a@b.c", "password": "pw"}

    def test_no_account_configured(self):
        with patch(f"{_SOURCE}.config", _config({})):
            assert zlib.account_configured() is False
            assert zlib.session_cookies(_MIRRORS[0]) is None


class TestHandler:
    _ACCOUNT = {"ZLIBRARY_REMIX_USERID": "42", "ZLIBRARY_REMIX_USERKEY": "k"}

    def test_link_comes_from_the_first_mirror_that_gives_one(self):
        refused = _response({"success": 0, "error": "Daily limit reached"})
        granted = _response({"success": 1, "file": {"downloadLink": "https://dl.example/f.pdf"}})
        with (
            patch(f"{_SOURCE}.config", _config(self._ACCOUNT)),
            patch(f"{_HANDLER}.configured_mirrors", return_value=_MIRRORS),
            patch(f"{_SOURCE}.requests.request", side_effect=[refused, granted]) as request,
        ):
            assert resolve_download_link("1", "abc") == "https://dl.example/f.pdf"
        assert request.call_args.args == ("GET", "https://z-lib.gl/eapi/book/1/abc/file")
        assert request.call_args.kwargs["cookies"] == {"remix_userid": "42", "remix_userkey": "k"}

    def test_every_refusal_is_reported(self):
        refused = _response({"success": 0, "error": "Daily limit reached"})
        with (
            patch(f"{_SOURCE}.config", _config(self._ACCOUNT)),
            patch(f"{_HANDLER}.configured_mirrors", return_value=_MIRRORS[:1]),
            patch(f"{_SOURCE}.requests.request", return_value=refused),
            pytest.raises(zlib.ZLibraryError, match="Daily limit reached"),
        ):
            resolve_download_link("1", "abc")

    def test_download_without_an_account_says_what_to_set(self):
        statuses = []
        task = SimpleNamespace(task_id="zlibrary:1:abc")
        with patch(f"{_SOURCE}.config", _config({})):
            result = ZLibraryHandler().download(
                task, threading.Event(), lambda _p: None, lambda s, m: statuses.append((s, m))
            )
        assert result is None
        assert statuses[-1] == ("error", NO_ACCOUNT_MESSAGE)

    def test_download_streams_the_resolved_link(self):
        task = SimpleNamespace(task_id="zlibrary:1:abc", format="pdf")
        with (
            patch(f"{_HANDLER}.resolve_download_link", return_value="https://dl.example/f.pdf"),
            patch(f"{_HANDLER}.download_public_file", return_value="/tmp/f.pdf") as download,
        ):
            result = ZLibraryHandler().download(
                task, threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result == "/tmp/f.pdf"
        assert download.call_args.args[0] == "https://dl.example/f.pdf"
