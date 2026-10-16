"""Tests for the per-source release search helper extracted from ``/api/releases``."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import release_search
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError


def _book(**overrides: Any) -> BookMetadata:
    fields: dict[str, Any] = {
        "provider": "manual",
        "provider_id": "1",
        "title": "Dungeon Crawler Carl",
        "authors": ["Matt Dinniman"],
        "isbn_13": "9780593820247",
    }
    fields.update(overrides)
    return BookMetadata(**fields)


class _Source:
    def __init__(self, result: list[str] | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else []
        self.error = error
        self.calls: list[tuple[Any, Any, bool, str]] = []

    def search(
        self, book: BookMetadata, plan: Any, *, expand_search: bool, content_type: str
    ) -> list[str]:
        self.calls.append((book, plan, expand_search, content_type))
        if self.error is not None:
            raise self.error
        return list(self.result)


def test_unknown_source_returns_error_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> Any:
        msg = f"Unknown source: {name}"
        raise ValueError(msg)

    monkeypatch.setattr("shelfmark.release_sources.get_source", _raise)

    source, releases, error = release_search.search_source_releases("nope", _book())

    assert source is None
    assert releases == []
    assert error == "Unknown source: nope"


@pytest.mark.parametrize(
    "exc",
    [SourceUnavailableError("down"), OSError("boom"), RuntimeError("bad")],
    ids=["unavailable", "oserror", "runtime"],
)
def test_source_failures_become_messages(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    src = _Source(error=exc)
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    source, releases, error = release_search.search_source_releases("s", _book())

    assert source is None
    assert releases == []
    assert error == f"s: {exc!s}"


def test_success_returns_source_and_forwards_search_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _Source(result=["r1", "r2"])
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    source, releases, error = release_search.search_source_releases(
        "s", _book(), expand_search=True, content_type="audiobook"
    )

    assert source is src
    assert releases == ["r1", "r2"]
    assert error is None
    book, plan, expand_search, content_type = src.calls[0]
    assert book.title == "Dungeon Crawler Carl"
    assert "dungeon crawler carl" in plan.primary_query.lower()
    assert plan.isbn_candidates == ["9780593820247"]
    assert expand_search is True
    assert content_type == "audiobook"


def test_plan_receives_filters_and_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    real_build = release_search.build_release_search_plan

    def _build(book: BookMetadata, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_build(book, **kwargs)

    monkeypatch.setattr(release_search, "build_release_search_plan", _build)
    src = _Source()
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    release_search.search_source_releases(
        "s",
        _book(),
        languages=["en"],
        manual_query="dcc",
        indexers=["idx"],
        user_id=42,
    )

    assert captured == {
        "languages": ["en"],
        "manual_query": "dcc",
        "indexers": ["idx"],
        "source_filters": None,
        "user_id": 42,
    }


class TestFilterReleases:
    """The browse language and format filters, applied to every source."""

    @staticmethod
    def _release(title: str, *, language: str | None = None, fmt: str | None = None):
        from shelfmark.release_sources import Release

        return Release(
            source="searxng", source_id=title, title=title, language=language, format=fmt
        )

    def test_known_other_languages_are_dropped_and_unknown_kept(self):
        releases = [
            self._release("greek", language="el"),
            self._release("greek-name", language="Greek"),
            self._release("english", language="en"),
            self._release("unknown"),
        ]

        kept = release_search.filter_releases(releases, languages=["el"], formats=None)

        assert [r.title for r in kept] == ["greek", "greek-name", "unknown"]

    def test_all_means_no_language_filter(self):
        releases = [self._release("english", language="en")]

        assert release_search.filter_releases(releases, languages=["all"], formats=None) == releases

    def test_formats_filter(self):
        releases = [
            self._release("epub", fmt="epub"),
            self._release("pdf", fmt="PDF"),
            self._release("unknown"),
        ]

        kept = release_search.filter_releases(releases, languages=None, formats=["epub"])

        assert [r.title for r in kept] == ["epub", "unknown"]


class TestBrowsePaging:
    """Which sources page a browse search."""

    def test_file_sources_page(self):
        from shelfmark.release_sources import source_supports_browse_paging

        for name in ("direct_download", "libgen", "searxng", "internet_archive"):
            assert source_supports_browse_paging(name) is True, name

    def test_other_and_unknown_sources_do_not(self):
        from shelfmark.release_sources import source_supports_browse_paging

        assert source_supports_browse_paging("prowlarr") is False
        assert source_supports_browse_paging("no-such-source") is False


class TestBrowsePageAndQueries:
    """Shared helpers that file sources use to read a search plan."""

    @staticmethod
    def _plan(manual_query: str | None = None, page: int | None = None):
        from shelfmark.core.models import SearchFilters
        from shelfmark.core.search_plan import ReleaseSearchPlan

        return ReleaseSearchPlan(
            languages=None,
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
            manual_query=manual_query,
            source_filters=SearchFilters(page=page) if page is not None else None,
        )

    @pytest.mark.parametrize(
        ("page", "expected"),
        [(None, 1), (1, 1), (0, 1), (-1, 1), (4, 4)],
        ids=["no-filters", "first", "zero", "negative", "fourth"],
    )
    def test_browse_page(self, page, expected):
        from shelfmark.release_sources import browse_page

        assert browse_page(self._plan(page=page)) == expected

    def test_blank_manual_query_falls_back_to_the_book_title(self):
        from shelfmark.release_sources import plan_query_candidates

        assert plan_query_candidates(self._plan(manual_query="   "), _book(title="Dune")) == [
            "Dune"
        ]
