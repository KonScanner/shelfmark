"""GET /api/releases browse searches: one source, a page, and the file filters."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.release_sources import Release, _default_column_config


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    test_client = main_module.app.test_client()
    with test_client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7
    return test_client


class _Source:
    def __init__(self, releases: list[Release], *, paging: bool = True) -> None:
        self.releases = releases
        self.supports_browse_paging = paging
        self.pages: list[int | None] = []

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        self.pages.append(plan.source_filters.page if plan.source_filters else None)
        return self.releases

    def get_column_config(self):
        return _default_column_config()


def _request(client, main_module, source: _Source, **params):
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.release_sources.get_source", return_value=source),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=True),
        patch(
            "shelfmark.release_sources.source_supports_browse_paging",
            return_value=source.supports_browse_paging,
        ),
    ):
        return client.get(
            "/api/releases", query_string={"source": "searxng", "query": "Ο Σωτήρ", **params}
        )


def test_language_and_format_filters_apply_to_any_source(client, main_module):
    source = _Source(
        [
            Release(source="searxng", source_id="el", title="el", language="el", format="pdf"),
            Release(source="searxng", source_id="en", title="en", language="en", format="pdf"),
            Release(source="searxng", source_id="mobi", title="mobi", format="mobi"),
            Release(source="searxng", source_id="unknown", title="unknown", format="pdf"),
        ]
    )

    resp = _request(client, main_module, source, lang="el", format=["pdf", "epub"])

    assert [r["title"] for r in resp.get_json()["releases"]] == ["el", "unknown"]


def test_first_page_sends_no_page_filter(client, main_module):
    source = _Source([Release(source="searxng", source_id="a", title="A")])

    body = _request(client, main_module, source).get_json()

    assert source.pages == [None]
    assert body["page"] == 1
    assert body["has_more"] is True


def test_later_page_reaches_a_source_that_pages(client, main_module):
    source = _Source([Release(source="searxng", source_id="a", title="A")])

    body = _request(client, main_module, source, page="3").get_json()

    assert source.pages == [3]
    assert body["page"] == 3
    assert body["has_more"] is True


def test_later_page_skips_a_source_that_cannot_page(client, main_module):
    source = _Source([Release(source="searxng", source_id="a", title="A")], paging=False)

    resp = _request(client, main_module, source, page="2")

    assert resp.status_code == 200
    body = resp.get_json()
    assert source.pages == []
    assert body["releases"] == []
    assert body["has_more"] is False


def test_an_empty_page_ends_the_paging(client, main_module):
    body = _request(client, main_module, _Source([]), page="2").get_json()

    assert body["has_more"] is False


def test_config_reports_the_file_settings(client, main_module):
    values = {
        "LANGUAGE_SORT_ORDER": "en, Greek, gre, xx, de",
        "FILES_PAGES_PER_SEARCH": 99,
        "FILES_SEARCH_TIMING": "on_open",
    }
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch.object(
            main_module.app_config,
            "get",
            side_effect=lambda key, default=None, **_kw: values.get(key, default),
        ),
    ):
        body = client.get("/api/config").get_json()

    # Names and three-letter codes resolve; repeats and unknown codes are dropped.
    assert body["language_sort_order"] == ["en", "el", "de"]
    assert body["default_sort"] == "language"
    assert body["files_pages_per_search"] == 5
    assert body["files_search_timing"] == "on_open"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("abc", None), ("1", None), ("0", None), ("-3", None), ("2", 2)],
    ids=["missing", "empty", "text", "first", "zero", "negative", "second"],
)
def test_browse_page_parsing(main_module, value, expected):
    assert main_module._parse_browse_page(value) == expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(0, 1), (-2, 1), ("abc", 2), (3, 3), (99, 5)],
    ids=["zero", "negative", "text", "inside", "above-max"],
)
def test_pages_per_search_is_clamped(main_module, stored, expected):
    with patch.object(
        main_module.app_config,
        "get",
        side_effect=lambda key, default=None, **_kw: (
            stored if key == "FILES_PAGES_PER_SEARCH" else default
        ),
    ):
        assert main_module._files_pages_per_search() == expected
