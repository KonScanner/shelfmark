"""A browse search for a later page asks Anna's Archive for that page."""

from shelfmark.core.models import SearchFilters
from shelfmark.release_sources.direct_download import annas_archive as aa


def _requested_url(monkeypatch, filters: SearchFilters) -> str:
    urls: list[str] = []

    def fake_fetch(url, _selector):
        urls.append(url)
        return "No files found.", None

    monkeypatch.setattr(aa.network, "get_aa_base_url", lambda: "https://mirror.example")
    monkeypatch.setattr(aa.network, "AAMirrorSelector", lambda: object())
    monkeypatch.setattr(aa, "_fetch_search_table", fake_fetch)
    aa.search_books("dune", filters)
    return urls[0]


def test_first_page_by_default(monkeypatch):
    assert "/search?index=&page=1&" in _requested_url(monkeypatch, SearchFilters())


def test_later_page(monkeypatch):
    assert "/search?index=&page=3&" in _requested_url(monkeypatch, SearchFilters(page=3))
