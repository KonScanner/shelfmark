"""download_public_file: streams a public URL into TMP_DIR and always closes it."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download import public_file

_MODULE = "shelfmark.download.public_file"


def _response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.is_redirect = False
    response.headers = {"content-length": str(len(body))}
    response.iter_content.return_value = [body]
    response.__enter__.return_value = response
    return response


@pytest.fixture(autouse=True)
def _public_hosts():
    with patch(f"{_MODULE}.prepare_public_url", side_effect=lambda url: url):
        yield


@pytest.mark.parametrize(
    ("body", "staged"),
    [(b"x" * 4096, True), (b"tiny", False)],
    ids=["full-file", "stub-too-small"],
)
def test_response_is_closed_on_every_path(tmp_path, body, staged):
    response = _response(body)
    with (
        patch(f"{_MODULE}.TMP_DIR", tmp_path),
        patch(f"{_MODULE}.requests.get", return_value=response),
    ):
        path = public_file.download_public_file(
            "https://example.com/book.epub",
            threading.Event(),
            lambda _p: None,
            lambda _s, _m: None,
            source_label="Internet Archive",
        )

    response.__exit__.assert_called_once()
    assert (path is not None) is staged
    if path is not None:
        assert path.endswith(".epub")
        assert "internet_archive_" in path


def test_cancel_before_body_closes_the_response(tmp_path):
    response = _response(b"x" * 4096)
    cancel = threading.Event()
    cancel.set()
    with (
        patch(f"{_MODULE}.TMP_DIR", tmp_path),
        patch(f"{_MODULE}.requests.get", return_value=response),
    ):
        # Cancelled before the request: nothing is opened at all.
        assert (
            public_file.download_public_file(
                "https://example.com/book.epub",
                cancel,
                lambda _p: None,
                lambda _s, _m: None,
                source_label="SearXNG",
            )
            is None
        )
    response.__enter__.assert_not_called()
    assert list(tmp_path.iterdir()) == []
