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


def _redirect(location: str) -> MagicMock:
    response = MagicMock()
    response.is_redirect = True
    response.headers = {"location": location}
    return response


def test_trusted_origin_skips_the_public_check_and_gets_its_login():
    final = _response(b"x" * 4096)
    trusted = {"http://calibre.lan:8083": ("reader", "secret")}
    with (
        patch(f"{_MODULE}.prepare_public_url", return_value=None) as public_check,
        patch(f"{_MODULE}.requests.get", return_value=final) as get,
    ):
        response = public_file._open_public_stream("http://calibre.lan:8083/opds/1.epub", trusted)

    assert response is final
    public_check.assert_not_called()
    assert get.call_args.kwargs["auth"] == ("reader", "secret")


def test_redirect_away_from_a_trusted_origin_drops_the_login_and_is_checked():
    trusted = {"http://calibre.lan:8083": ("reader", "secret")}
    final = _response(b"x" * 4096)
    with (
        patch(f"{_MODULE}.prepare_public_url", side_effect=lambda url: url) as public_check,
        patch(
            f"{_MODULE}.requests.get",
            side_effect=[_redirect("https://cdn.example.com/1.epub"), final],
        ) as get,
    ):
        public_file._open_public_stream("http://calibre.lan:8083/opds/1.epub", trusted)

    public_check.assert_called_once_with("https://cdn.example.com/1.epub")
    assert get.call_args_list[1].kwargs["auth"] is None


def test_an_untrusted_private_url_is_refused():
    with patch(f"{_MODULE}.prepare_public_url", return_value=None):
        assert public_file._open_public_stream("http://10.0.0.5/book.epub", {}) is None


def test_url_origin_drops_credentials_and_path():
    assert public_file.url_origin("HTTPS://user:pw@Host.Example:8443/a/b?c=1") == (
        "https://host.example:8443"
    )


@pytest.mark.parametrize(
    ("headers", "final_url", "url", "fallback", "expected"),
    [
        (
            {"content-disposition": 'attachment; filename="Stivenson.215081.fb2.zip"'},
            "https://static.flibusta.is/b.fb2/x",
            "https://flibusta.is/b/215081/fb2",
            "fb2",
            ".zip",
        ),
        (
            {"content-disposition": "attachment; filename*=UTF-8''%CE%9F%CE%B4.epub"},
            "",
            "https://x/get",
            None,
            ".epub",
        ),
        (
            {},
            "https://g.org/cache/epub/1727/pg1727-images-3.epub",
            "https://g.org/1727.epub3.images",
            "epub",
            ".epub",
        ),
        ({}, "https://x/b/7/epub", "https://x/b/7/epub", "epub", ".epub"),
        ({}, "", "https://x/download?id=1", None, ".bin"),
        ({}, "", "https://x/download?id=1", "exe", ".bin"),
    ],
    ids=[
        "disposition-zip",
        "disposition-rfc2231",
        "final-url",
        "fallback",
        "unknown",
        "bad-fallback",
    ],
)
def test_file_suffix_prefers_the_most_reliable_name(headers, final_url, url, fallback, expected):
    response = MagicMock()
    response.headers = headers
    response.url = final_url
    assert public_file._file_suffix(response, url, fallback) == expected
