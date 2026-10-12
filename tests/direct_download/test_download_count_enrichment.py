"""Tests for the Anna's Archive download-count enrichment of search results."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import annas_archive

_MODULE = "shelfmark.release_sources.direct_download.annas_archive"


@pytest.fixture(autouse=True)
def _empty_count_cache():
    annas_archive._download_count_cache.clear()
    yield
    annas_archive._download_count_cache.clear()


def _record(book_id: str) -> BrowseRecord:
    return BrowseRecord(id=book_id, title=f"Book {book_id}", source="direct_download")


class TestFetchDownloadCount:
    """Tests for one inline_info lookup."""

    def test_lookup_goes_through_configured_proxy(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"downloads_total": 42}
        session = MagicMock()
        session.get.return_value = response
        proxies = {"https": "socks5://proxy:1080"}

        with (
            patch(f"{_MODULE}.network.get_aa_base_url", return_value="https://aa.example"),
            patch(f"{_MODULE}.network.get_proxies", return_value=proxies),
            patch(f"{_MODULE}.network.get_ssl_verify", return_value=True),
        ):
            count = annas_archive._fetch_download_count_inline("abc", session)

        assert count == 42
        kwargs = session.get.call_args.kwargs
        assert kwargs["proxies"] == proxies
        assert kwargs["verify"] is True
        assert session.get.call_args.args[0] == "https://aa.example/dyn/md5/inline_info/abc"

    def test_lookup_failure_returns_none(self):
        session = MagicMock()
        session.get.side_effect = OSError("down")
        with patch(f"{_MODULE}.network.get_aa_base_url", return_value="https://aa.example"):
            assert annas_archive._fetch_download_count_inline("abc", session) is None


class TestEnrichSearchResults:
    """Tests for the batched enrichment."""

    def test_counts_added_to_records(self):
        books = [_record("a"), _record("b")]
        with patch(
            f"{_MODULE}._fetch_download_count_inline",
            side_effect=lambda bid, _session: {"a": 7, "b": None}[bid],
        ):
            annas_archive._enrich_search_results_with_downloads(books)

        assert books[0].info == {"Downloads": ["7"]}
        assert books[1].info is None

    def test_all_lookups_share_one_session(self):
        seen_sessions: list[object] = []
        lock = threading.Lock()

        def fake_fetch(_bid, session):
            with lock:
                seen_sessions.append(session)
            return 1

        with patch(f"{_MODULE}._fetch_download_count_inline", side_effect=fake_fetch):
            annas_archive._enrich_search_results_with_downloads([_record(str(i)) for i in range(8)])

        assert len(seen_sessions) == 8
        assert len({id(session) for session in seen_sessions}) == 1

    def test_slow_lookups_do_not_hold_the_search_past_the_budget(self):
        release = threading.Event()

        def fake_fetch(bid, _session):
            if bid == "slow":
                release.wait(timeout=5)
                return 99
            return 3

        books = [_record("fast"), _record("slow")]
        try:
            with (
                patch(f"{_MODULE}._DOWNLOAD_COUNT_BUDGET_SECONDS", 0.3),
                patch(f"{_MODULE}._fetch_download_count_inline", side_effect=fake_fetch),
            ):
                started = time.monotonic()
                annas_archive._enrich_search_results_with_downloads(books)
                elapsed = time.monotonic() - started
        finally:
            release.set()

        assert elapsed < 2.0
        assert books[0].info == {"Downloads": ["3"]}
        assert books[1].info is None

    def test_budget_capped_by_remaining_search_deadline(self):
        deadline = MagicMock()
        deadline.remaining = 1.5
        with patch(f"{_MODULE}.search_deadline.current", return_value=deadline):
            assert annas_archive._download_count_budget() == 1.5

    def test_spent_search_deadline_skips_lookups(self):
        deadline = MagicMock()
        deadline.remaining = 0.0
        with (
            patch(f"{_MODULE}.search_deadline.current", return_value=deadline),
            patch(f"{_MODULE}._fetch_download_count_inline") as fake_fetch,
        ):
            annas_archive._enrich_search_results_with_downloads([_record("a")])

        fake_fetch.assert_not_called()


class TestDownloadCountCache:
    """Counts are reused, and late lookups still pay off for the next search."""

    def test_cached_count_is_used_without_a_lookup(self):
        annas_archive._store_download_count("a", 12)
        books = [_record("a")]
        with patch(f"{_MODULE}._fetch_download_count_inline") as fake_fetch:
            annas_archive._enrich_search_results_with_downloads(books)

        fake_fetch.assert_not_called()
        assert books[0].info == {"Downloads": ["12"]}

    def test_expired_count_is_looked_up_again(self):
        annas_archive._store_download_count("a", 12)
        stored_at, count = annas_archive._download_count_cache["a"]
        annas_archive._download_count_cache["a"] = (
            stored_at - annas_archive._DOWNLOAD_COUNT_CACHE_TTL_SECONDS - 1,
            count,
        )

        assert annas_archive._cached_download_count("a") is None

    def test_cache_is_bounded(self):
        with patch(f"{_MODULE}._DOWNLOAD_COUNT_CACHE_MAX_ENTRIES", 3):
            for i in range(5):
                annas_archive._store_download_count(str(i), i)

        assert list(annas_archive._download_count_cache) == ["2", "3", "4"]

    def test_lookup_that_misses_the_budget_fills_the_cache(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"downloads_total": 77}
        session = MagicMock()

        def slow_get(*_args, **_kwargs):
            time.sleep(0.5)
            return response

        session.get.side_effect = slow_get

        with (
            patch(f"{_MODULE}._DOWNLOAD_COUNT_BUDGET_SECONDS", 0.05),
            patch(f"{_MODULE}.requests.Session", return_value=session),
            patch(f"{_MODULE}.network.get_aa_base_url", return_value="https://aa.example"),
            patch(f"{_MODULE}.network.get_proxies", return_value={}),
            patch(f"{_MODULE}.network.get_ssl_verify", return_value=True),
        ):
            first = [_record("late")]
            annas_archive._enrich_search_results_with_downloads(first)
            assert first[0].info is None

            waited = 0.0
            while annas_archive._cached_download_count("late") is None and waited < 5:
                time.sleep(0.05)
                waited += 0.05

            second = [_record("late")]
            annas_archive._enrich_search_results_with_downloads(second)

        assert second[0].info == {"Downloads": ["77"]}
        assert session.get.call_count == 1

    def test_disabled_counts_skip_every_lookup(self):
        books = [_record("a")]
        with (
            patch(f"{_MODULE}._fetch_download_count_inline") as fake_fetch,
            annas_archive.download_counts_disabled(),
        ):
            annas_archive._enrich_search_results_with_downloads(books)

        fake_fetch.assert_not_called()
        assert books[0].info is None
        assert annas_archive._skip_download_counts.get() is False


def test_warmup_search_runs_without_download_counts(monkeypatch):
    from shelfmark.download import warmup
    from shelfmark.release_sources import direct_download as dd

    seen: list[bool] = []

    def fake_search(_query, _filters):
        seen.append(annas_archive._skip_download_counts.get())
        return [object()], 1

    monkeypatch.setattr(warmup, "_user_search_seen", threading.Event())
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    monkeypatch.setattr(dd, "search_books", fake_search)

    assert warmup.run_warmup() is True
    assert seen == [True]
