"""Z-Library release source, over the JSON API its apps use (``/eapi``).

Search needs no account. A download does: the file link comes from an account's
session, and each account has a daily download limit. The mirrors are the Z-Library
mirrors already configured for Direct Download (``ZLIB_MIRROR_URLS``); Z-Library's
domains are blocked often, so each call tries them in order.
"""

import re
import threading
import time
from typing import TYPE_CHECKING, Any, ClassVar

import requests

from shelfmark.core import mirrors
from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download import http as downloader
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.release_sources import (
    BrowseRecord,
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceUnavailableError,
    browse_page,
    plan_query_candidates,
    register_source,
    wanted_formats,
)

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_DEFAULT_MAX_RESULTS = 50
_DEFAULT_TIMEOUT = 20
_SESSION_SECONDS = 12 * 3600.0

# A release id comes back from the client in a queue entry; anything else is refused.
_SOURCE_ID = re.compile(r"zlibrary:(\d+):([0-9A-Za-z]+)")


class ZLibraryError(Exception):
    """Z-Library answered with an error, or no mirror answered at all."""


def split_source_id(source_id: str) -> tuple[str, str] | None:
    """Split a release id ``zlibrary:<book id>:<hash>``, or return None."""
    match = _SOURCE_ID.fullmatch(source_id or "")
    return (match.group(1), match.group(2)) if match else None


def _timeout() -> int:
    return coerce_int(config.get("ZLIBRARY_TIMEOUT", _DEFAULT_TIMEOUT), _DEFAULT_TIMEOUT)


def _headers() -> dict[str, str]:
    return {"User-Agent": downloader.DOWNLOAD_HEADERS["User-Agent"], "Accept": "application/json"}


def call_api(
    method: str,
    path: str,
    *,
    data: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
    base: str,
) -> dict[str, Any]:
    """Call one eAPI endpoint on one mirror and return its JSON answer.

    Raises:
        ZLibraryError: the request failed, or the answer was not ``success``.
    """
    url = f"{base.rstrip('/')}{path}"
    try:
        response = requests.request(
            method,
            url,
            data=data,
            cookies=cookies,
            headers=_headers(),
            timeout=_timeout(),
            proxies=get_proxies(url),
            verify=get_ssl_verify(url),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise ZLibraryError(f"{url}: {error}") from error
    if not isinstance(payload, dict) or payload.get("success") != 1:
        detail = (
            payload.get("error") or payload.get("message") if isinstance(payload, dict) else None
        )
        raise ZLibraryError(str(detail or f"{url} returned no success"))
    return payload


def configured_mirrors() -> list[str]:
    return mirrors.get_zlib_mirrors()


_sessions: dict[str, tuple[float, dict[str, str]]] = {}
_session_lock = threading.Lock()


def forget_session(base: str) -> None:
    """Drop a cached login, for example after the mirror refused it."""
    with _session_lock:
        _sessions.pop(base, None)


def account_configured() -> bool:
    """Whether a Z-Library account (login or session cookies) is configured."""
    has_cookies = all(
        str(config.get(key, "") or "").strip()
        for key in ("ZLIBRARY_REMIX_USERID", "ZLIBRARY_REMIX_USERKEY")
    )
    has_login = bool(str(config.get("ZLIBRARY_EMAIL", "") or "").strip()) and bool(
        config.get("ZLIBRARY_PASSWORD", "")
    )
    return has_cookies or has_login


def session_cookies(base: str) -> dict[str, str] | None:
    """Return the account cookies for a mirror, logging in when needed.

    Returns None when no account is configured.

    Raises:
        ZLibraryError: the login failed.
    """
    user_id = str(config.get("ZLIBRARY_REMIX_USERID", "") or "").strip()
    user_key = str(config.get("ZLIBRARY_REMIX_USERKEY", "") or "").strip()
    if user_id and user_key:
        return {"remix_userid": user_id, "remix_userkey": user_key}

    email = str(config.get("ZLIBRARY_EMAIL", "") or "").strip()
    password = str(config.get("ZLIBRARY_PASSWORD", "") or "")
    if not email or not password:
        return None

    with _session_lock:
        cached = _sessions.get(base)
    if cached and time.monotonic() - cached[0] < _SESSION_SECONDS:
        return cached[1]

    payload = call_api(
        "POST", "/eapi/user/login", data={"email": email, "password": password}, base=base
    )
    raw_user = payload.get("user")
    user = raw_user if isinstance(raw_user, dict) else {}
    cookies = {
        "remix_userid": str(user.get("id") or ""),
        "remix_userkey": str(user.get("remix_userkey") or ""),
    }
    if not all(cookies.values()):
        msg = "Z-Library login answered without a user id and key"
        raise ZLibraryError(msg)
    with _session_lock:
        _sessions[base] = (time.monotonic(), cookies)
    return cookies


def _book_to_release(book: dict[str, Any]) -> Release | None:
    book_id = str(book.get("id") or "")
    book_hash = str(book.get("hash") or "")
    source_id = f"zlibrary:{book_id}:{book_hash}"
    if split_source_id(source_id) is None or not book.get("title"):
        return None
    language_name = str(book.get("language") or "").strip()
    language = normalize_language(language_name) if language_name else None
    fmt = str(book.get("extension") or "").strip().lower() or None
    year = book.get("year")
    md5 = str(book.get("md5") or "").strip().lower() or None
    return Release(
        source="zlibrary",
        source_id=source_id,
        title=str(book["title"]),
        format=fmt,
        language=language,
        size=str(book.get("filesizeString") or "") or None,
        download_url=None,  # The link comes from an account session at download time.
        info_url=str(book.get("href") or "") or None,
        protocol=ReleaseProtocol.HTTP,
        indexer="Z-Library",
        content_type="ebook",
        extra={
            "author": str(book.get("author") or "") or None,
            "year": str(year) if year else None,
            "language": language,
            "preview": str(book.get("cover") or "") or None,
            # The same file often comes from Anna's Archive and Libgen too; the md5
            # lets the Files tab merge those rows.
            "md5": md5,
            "publisher": str(book.get("publisher") or "") or None,
        },
    )


def search_books(query: str, page: int, limit: int, formats: list[str]) -> list[dict[str, Any]]:
    """Search the first mirror that answers and return its book list.

    Raises:
        ZLibraryError: no mirror answered.
    """
    data: dict[str, Any] = {"message": query, "limit": limit, "page": page}
    if formats:
        data["extensions[]"] = formats
    errors: list[str] = []
    for base in configured_mirrors():
        try:
            payload = call_api("POST", "/eapi/book/search", data=data, base=base)
        except ZLibraryError as error:
            errors.append(str(error))
            continue
        books = payload.get("books")
        return [book for book in books if isinstance(book, dict)] if isinstance(books, list) else []
    msg = "No Z-Library mirror answered: " + "; ".join(errors or ["no mirror configured"])
    raise ZLibraryError(msg)


@register_source("zlibrary")
class ZLibrarySource(ReleaseSource):
    """Release source that searches Z-Library."""

    name = "zlibrary"
    display_name = "Z-Library"
    supports_browse_paging: ClassVar[bool] = True
    supported_content_types: ClassVar[list[str]] = ["ebook"]

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Z-Library; the first query text that finds anything wins.

        Raises:
            SourceUnavailableError: no mirror answered.
        """
        if content_type != "ebook" or not self.is_available():
            return []
        limit = coerce_int(
            config.get("ZLIBRARY_MAX_RESULTS", _DEFAULT_MAX_RESULTS), _DEFAULT_MAX_RESULTS
        )
        page = browse_page(plan)
        formats = wanted_formats(plan)
        for index, query in enumerate(plan_query_candidates(plan, book)):
            try:
                books = search_books(query, page, limit, formats)
            except ZLibraryError as error:
                logger.warning("Z-Library search failed for '%s': %s", query, error)
                if index == 0:
                    raise SourceUnavailableError(str(error)) from error
                return []
            releases = [
                release for item in books if (release := _book_to_release(item)) is not None
            ]
            logger.info("Z-Library search '%s' page %d: %d release(s)", query, page, len(releases))
            if releases:
                return releases
        return []

    def is_available(self) -> bool:
        """Return True when the source is enabled and a Z-Library mirror is configured."""
        return config.get("ZLIBRARY_SEARCH_ENABLED", False) is True and bool(configured_mirrors())

    def search_results_are_releases(self) -> bool:
        """Each search result is one downloadable file."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Rebuild a minimal browse record from its id, without a network round trip."""
        if split_source_id(record_id) is None:
            return None
        return BrowseRecord(id=record_id, title=record_id, source=self.name)

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language, format and size columns for the release list."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0, 2fr) 60px 80px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.THUMBNAIL, key="extra.preview"),
            supported_filters=["format", "language"],
        )
