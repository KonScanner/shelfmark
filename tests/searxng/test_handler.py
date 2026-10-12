"""Tests for the SearXNG download handler."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.searxng.handler import SearXNGHandler

_PUBLIC_HOSTS = {"example.com", "cdn.example.org"}


def _fake_prepare_public_url(url: str) -> str | None:
    """Treat only the test's public hosts as public, without real DNS."""
    from urllib.parse import urlparse

    return url if urlparse(url).hostname in _PUBLIC_HOSTS else None


@pytest.fixture(autouse=True)
def _offline_public_url_check():
    with patch(
        "shelfmark.release_sources.searxng.handler.prepare_public_url",
        side_effect=_fake_prepare_public_url,
    ):
        yield


def _task(url: str = "https://example.com/book.pdf") -> DownloadTask:
    return DownloadTask(
        task_id=url,
        source="searxng",
        title="Test Book",
        source_url=url,
        format="pdf",
    )


def _response(chunks: list[bytes], total: int | None = None) -> MagicMock:
    response = MagicMock()
    response.is_redirect = False
    response.headers = {}
    if total is not None:
        response.headers = {"content-length": str(total)}
    response.iter_content.return_value = iter(chunks)
    return response


class TestDownload:
    """Tests for the download flow."""

    def test_stages_file_in_tmp_dir(self, tmp_path):
        payload = _response([b"a" * 2048], total=2048)
        task = _task()
        with (
            patch("shelfmark.release_sources.searxng.handler.TMP_DIR", tmp_path),
            patch("shelfmark.release_sources.searxng.handler.requests.get", return_value=payload),
        ):
            result = SearXNGHandler().download(
                task, threading.Event(), lambda _p: None, lambda _s, _m: None
            )

        assert result is not None
        staged = tmp_path / result.split("/")[-1]
        assert staged.exists()
        assert staged.stat().st_size == 2048
        assert staged.suffix == ".pdf"

    def test_missing_url_fails(self):
        task = _task(url="")
        status_calls: list[tuple[str, str | None]] = []
        result = SearXNGHandler().download(
            task, threading.Event(), lambda _p: None, lambda s, m: status_calls.append((s, m))
        )
        assert result is None
        assert status_calls[0][0] == "error"

    def test_too_small_file_rejected(self, tmp_path):
        payload = _response([b"ab"], total=2)
        with (
            patch("shelfmark.release_sources.searxng.handler.TMP_DIR", tmp_path),
            patch("shelfmark.release_sources.searxng.handler.requests.get", return_value=payload),
        ):
            result = SearXNGHandler().download(
                _task(), threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result is None
        assert list(tmp_path.iterdir()) == []

    def test_cancellation_cleans_up(self, tmp_path):
        cancel_flag = threading.Event()
        cancel_flag.set()
        payload = _response([b"a" * 4096], total=4096)
        with (
            patch("shelfmark.release_sources.searxng.handler.TMP_DIR", tmp_path),
            patch("shelfmark.release_sources.searxng.handler.requests.get", return_value=payload),
        ):
            result = SearXNGHandler().download(
                _task(), cancel_flag, lambda _p: None, lambda _s, _m: None
            )
        assert result is None
        assert list(tmp_path.iterdir()) == []

    def test_request_error_reports_error(self):
        import requests as requests_module

        status_calls: list[tuple[str, str | None]] = []
        with patch(
            "shelfmark.release_sources.searxng.handler.requests.get",
            side_effect=requests_module.ConnectionError("down"),
        ):
            result = SearXNGHandler().download(
                _task(),
                threading.Event(),
                lambda _p: None,
                lambda s, m: status_calls.append((s, m)),
            )
        assert result is None
        assert status_calls[-1][0] == "error"


class TestPublicHostGuard:
    """The handler must never fetch the container's own network."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8084/api/settings",
            "http://host.docker.internal:47821/search.pdf",
            "http://169.254.169.254/latest/meta-data.pdf",
        ],
    )
    def test_non_public_url_blocked_without_request(self, url):
        status_calls: list[tuple[str, str | None]] = []
        with patch("shelfmark.release_sources.searxng.handler.requests.get") as fake_get:
            result = SearXNGHandler().download(
                _task(url),
                threading.Event(),
                lambda _p: None,
                lambda s, m: status_calls.append((s, m)),
            )
        assert result is None
        fake_get.assert_not_called()
        assert status_calls[-1][0] == "error"

    def test_redirect_to_private_host_blocked(self):
        redirect = MagicMock()
        redirect.is_redirect = True
        redirect.headers = {"location": "http://10.0.0.5/secret.pdf"}
        with patch(
            "shelfmark.release_sources.searxng.handler.requests.get", return_value=redirect
        ) as fake_get:
            result = SearXNGHandler().download(
                _task(), threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result is None
        assert fake_get.call_count == 1

    def test_public_redirect_followed(self, tmp_path):
        redirect = MagicMock()
        redirect.is_redirect = True
        redirect.headers = {"location": "https://cdn.example.org/book.pdf"}
        payload = _response([b"a" * 2048], total=2048)
        with (
            patch("shelfmark.release_sources.searxng.handler.TMP_DIR", tmp_path),
            patch(
                "shelfmark.release_sources.searxng.handler.requests.get",
                side_effect=[redirect, payload],
            ) as fake_get,
        ):
            result = SearXNGHandler().download(
                _task(), threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert result is not None
        assert fake_get.call_args_list[1].args[0] == "https://cdn.example.org/book.pdf"

    def test_request_uses_configured_proxy(self, tmp_path):
        payload = _response([b"a" * 2048], total=2048)
        proxies = {"https": "socks5://proxy:1080", "http": "socks5://proxy:1080"}
        with (
            patch("shelfmark.release_sources.searxng.handler.TMP_DIR", tmp_path),
            patch("shelfmark.release_sources.searxng.handler.get_proxies", return_value=proxies),
            patch(
                "shelfmark.release_sources.searxng.handler.requests.get", return_value=payload
            ) as fake_get,
        ):
            SearXNGHandler().download(
                _task(), threading.Event(), lambda _p: None, lambda _s, _m: None
            )
        assert fake_get.call_args.kwargs["proxies"] == proxies
        assert fake_get.call_args.kwargs["allow_redirects"] is False
