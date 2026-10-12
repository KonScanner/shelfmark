"""Tests for targeted image cache safety and fetch fallbacks."""

import requests

from shelfmark.core.image_cache import ImageCacheService


def test_fetch_and_cache_rejects_backslash_authority_bypass_before_request(
    tmp_path, monkeypatch
) -> None:
    cache = ImageCacheService(tmp_path)
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        raise AssertionError("unsafe URL should not be requested")

    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-ssrf", "http://127.0.0.1:6666\\@1.1.1.1") is None
    assert calls == []
    assert "cover-ssrf" not in cache._index


def test_is_safe_url_rejects_encoded_separator_in_authority() -> None:
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%5c@1.1.1.1") is False
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%2f@1.1.1.1") is False


def test_is_safe_url_rejects_invalid_ipv6_url() -> None:
    assert ImageCacheService._is_safe_url("http://[") is False


def test_fetch_and_cache_blocks_unsafe_redirect(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_getaddrinfo(hostname, *_args, **_kwargs):
        addresses = {
            "example.com": "93.184.216.34",
            "127.0.0.1": "127.0.0.1",
        }
        return [(None, None, None, None, (addresses[hostname], 0))]

    class RedirectResponse:
        is_redirect = True
        headers = {"location": "http://127.0.0.1/cover.jpg"}

        def close(self):
            return None

    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return RedirectResponse()

    monkeypatch.setattr("shelfmark.download.network.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-redirect", "https://example.com/cover.jpg") is None
    assert calls == ["https://example.com/cover.jpg"]
    assert "cover-redirect" not in cache._index


def test_fetch_and_cache_returns_none_on_request_exception(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_get(*_args, **_kwargs):
        raise requests.exceptions.TooManyRedirects("too many redirects")

    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-1", "https://example.com/cover.jpg") is None
    assert "cover-1" not in cache._index


def test_cover_cache_key_depends_on_the_url_only() -> None:
    from shelfmark.core.image_cache import cover_cache_key

    key = cover_cache_key("https://example.com/cover.jpg")

    assert key == cover_cache_key("  https://example.com/cover.jpg \n")
    assert key != cover_cache_key("https://example.com/other.jpg")
    assert key != cover_cache_key("https://example.com/cover.jpg?size=large")
    assert key.isalnum() and key == key.lower()
    assert "/" not in key and "." not in key


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_fetch_sends_same_origin_referer_on_each_hop(tmp_path, monkeypatch) -> None:
    # libgen.li returns an empty 200 image without a Referer; each hop must name its own origin.
    cache = ImageCacheService(tmp_path)

    def fake_getaddrinfo(_hostname, *_args, **_kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    class RedirectResponse:
        is_redirect = True
        headers = {"location": "https://cdn.example.org/cover.png"}

        def close(self):
            return None

    class ImageResponse:
        is_redirect = False
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield _PNG_BYTES

    referers = []

    def fake_get(url, **kwargs):
        referers.append(kwargs["headers"]["Referer"])
        return RedirectResponse() if url.startswith("https://libgen.li") else ImageResponse()

    monkeypatch.setattr("shelfmark.download.network.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-ref", "https://libgen.li/covers/1/a_small.jpg") is not None
    assert referers == ["https://libgen.li/", "https://cdn.example.org/"]


def test_cache_hit_does_not_rewrite_index_within_persist_interval(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)
    assert cache.put("cover-a", _PNG_BYTES, "image/png")

    saves = []
    original_save = cache._save_index
    monkeypatch.setattr(cache, "_save_index", lambda: (saves.append(1), original_save()))

    for _ in range(20):
        assert cache.get("cover-a") is not None

    assert saves == []


def test_cache_hit_persists_access_time_once_it_is_stale(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)
    assert cache.put("cover-a", _PNG_BYTES, "image/png")
    cache._index["cover-a"]["accessed_at"] = 0

    saves = []
    original_save = cache._save_index
    monkeypatch.setattr(cache, "_save_index", lambda: (saves.append(1), original_save()))

    assert cache.get("cover-a") is not None
    assert cache.get("cover-a") is not None

    assert saves == [1]


def test_cache_miss_does_not_reparse_an_unchanged_index(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)
    assert cache.put("cover-a", _PNG_BYTES, "image/png")

    loads = []
    original_load = cache._load_index
    monkeypatch.setattr(cache, "_load_index", lambda: (loads.append(1), original_load()))

    for i in range(10):
        assert cache.get(f"missing-{i}") is None

    assert loads == []


def test_cache_miss_picks_up_an_index_changed_by_another_process(tmp_path) -> None:
    writer = ImageCacheService(tmp_path)
    reader = ImageCacheService(tmp_path)

    assert writer.put("cover-b", _PNG_BYTES, "image/png")

    assert reader.get("cover-b") == (_PNG_BYTES, "image/png")
