"""Tests for the Project Ben-Yehuda release source and its download handler."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.benyehuda import source as by
from shelfmark.release_sources.benyehuda.handler import BenYehudaHandler
from shelfmark.release_sources.benyehuda.source import BenYehudaSource

_SOURCE = "shelfmark.release_sources.benyehuda.source"
_HANDLER = "shelfmark.release_sources.benyehuda.handler"

TEXT = {
    "id": 3431,
    "url": "https://benyehuda.org/read/3431",
    "metadata": {
        "title": "בעיר ההרגה",
        "author_string": "חיים נחמן ביאליק",
        "orig_publication_date": "1904-01-01",
        "genre": "poetry",
    },
    "download_url": "https://benyehuda.org/download/3431?format=epub",
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


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    return response


_ENABLED = {"BENYEHUDA_ENABLED": True, "BENYEHUDA_API_KEY": "secret"}


class TestRegistration:
    def test_package_import_registers_source_and_handler(self):
        from shelfmark.release_sources import get_handler, get_source

        assert isinstance(get_source("benyehuda"), BenYehudaSource)
        assert isinstance(get_handler("benyehuda"), BenYehudaHandler)


class TestRelease:
    def test_text_maps_to_a_hebrew_release(self):
        release = by._text_to_release(TEXT, "epub")
        assert release is not None
        assert release.source_id == "benyehuda:3431:epub"
        assert release.title == "בעיר ההרגה"
        assert release.language == "he"
        assert release.extra["author"] == "חיים נחמן ביאליק"
        assert release.extra["year"] == "1904"
        assert release.download_url is None

    @pytest.mark.parametrize(
        "source_id", ["benyehuda:1:exe", "benyehuda:x:epub", "benyehuda:1:epub:2"]
    )
    def test_bad_ids_are_refused(self, source_id):
        assert by.split_source_id(source_id) is None


class TestSearch:
    def test_search_posts_the_key_query_and_format(self):
        values = {**_ENABLED, "BENYEHUDA_FILE_FORMAT": "pdf"}
        with (
            patch(f"{_SOURCE}.config", _config(values)),
            patch(
                f"{_SOURCE}.requests.request", return_value=_response({"data": [TEXT]})
            ) as request,
        ):
            (release,) = BenYehudaSource().search(_book(), _plan("ביאליק"))
        assert release.source_id == "benyehuda:3431:pdf"
        assert request.call_args.args == ("POST", "https://benyehuda.org/api/v1/search")
        assert request.call_args.kwargs["json"] == {
            "key": "secret",
            "view": "basic",
            "file_format": "pdf",
            "snippet": False,
            "fulltext": "ביאליק",
        }

    def test_failure_reports_the_source_unavailable(self):
        import requests

        with (
            patch(f"{_SOURCE}.config", _config(_ENABLED)),
            patch(f"{_SOURCE}.requests.request", side_effect=requests.ConnectionError("down")),
            pytest.raises(SourceUnavailableError, match="Ben-Yehuda"),
        ):
            BenYehudaSource().search(_book(), _plan("x"))

    def test_unavailable_without_a_key(self):
        with patch(f"{_SOURCE}.config", _config({"BENYEHUDA_ENABLED": True})):
            assert BenYehudaSource().is_available() is False

    def test_unknown_format_setting_falls_back_to_epub(self):
        with patch(f"{_SOURCE}.config", _config({"BENYEHUDA_FILE_FORMAT": "exe"})):
            assert by.file_format() == "epub"


class TestHandler:
    def test_asks_for_a_fresh_link_then_downloads_it(self):
        task = SimpleNamespace(task_id="benyehuda:3431:epub")
        with (
            patch(f"{_SOURCE}.config", _config(_ENABLED)),
            patch(f"{_SOURCE}.requests.request", return_value=_response(TEXT)) as request,
            patch(f"{_HANDLER}.download_public_file", return_value="/tmp/a.epub") as download,
        ):
            result = BenYehudaHandler().download(
                task, threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result == "/tmp/a.epub"
        assert request.call_args.args == ("GET", "https://benyehuda.org/api/v1/texts/3431")
        assert request.call_args.kwargs["params"] == {
            "key": "secret",
            "view": "basic",
            "file_format": "epub",
        }
        assert download.call_args.args[0] == TEXT["download_url"]

    def test_missing_link_is_an_error(self):
        statuses = []
        task = SimpleNamespace(task_id="benyehuda:3431:epub")
        with (
            patch(f"{_SOURCE}.config", _config(_ENABLED)),
            patch(f"{_SOURCE}.requests.request", return_value=_response({"id": 3431})),
        ):
            result = BenYehudaHandler().download(
                task, threading.Event(), lambda _p: None, lambda s, m: statuses.append((s, m))
            )
        assert result is None
        assert statuses[-1][0] == "error"
