"""Tests for the shared-pool sessions used by metadata providers."""

from unittest.mock import patch

from shelfmark.download import network


def test_sessions_share_one_connection_pool():
    first = network.new_pooled_session("https://api.example.com")
    second = network.new_pooled_session("https://other.example.com")

    assert first is not second
    assert first.get_adapter("https://api.example.com") is network._SHARED_HTTP_ADAPTER
    assert second.get_adapter("http://other.example.com") is network._SHARED_HTTP_ADAPTER


def test_session_headers_stay_per_session():
    first = network.new_pooled_session("https://api.example.com")
    second = network.new_pooled_session("https://api.example.com")

    first.headers["Authorization"] = "Bearer secret"

    assert "Authorization" not in second.headers


def test_session_uses_configured_proxy():
    proxies = {"http": "socks5://proxy:1080", "https": "socks5://proxy:1080"}
    with patch.object(network, "get_proxies", return_value=proxies) as get_proxies:
        session = network.new_pooled_session("https://api.example.com")

    get_proxies.assert_called_once_with("https://api.example.com")
    assert session.proxies == proxies


def test_session_without_proxy_config_sets_no_proxy():
    with patch.object(network, "get_proxies", return_value={}):
        session = network.new_pooled_session("https://api.example.com")

    assert session.proxies == {}


def test_metadata_providers_use_pooled_sessions():
    from shelfmark.metadata_providers.googlebooks import GoogleBooksProvider
    from shelfmark.metadata_providers.moly import MolyProvider
    from shelfmark.metadata_providers.openlibrary import OpenLibraryProvider

    for provider in (OpenLibraryProvider(), MolyProvider(), GoogleBooksProvider(api_key="k")):
        adapter = provider.session.get_adapter("https://example.com")
        assert adapter is network._SHARED_HTTP_ADAPTER, type(provider).__name__
