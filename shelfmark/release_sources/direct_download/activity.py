"""Anna's Archive community activity: comments, reactions and file reports, grouped by file.

The ``/activity`` page is the only place Anna's Archive lists this. It has no paging: the
"latest" view always shows the newest ~100 entries, and the "randomize" view shows a fresh
random set of file reports on each load. Comments and reactions name their file by md5
only; file reports also carry the file's title, author and format line.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from bs4 import BeautifulSoup, Tag

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources.direct_download.annas_archive import (
    SearchUnavailableError,
    ensure_available,
)
from shelfmark.release_sources.direct_download.common import html_response_text

logger = setup_logger(__name__)

ActivityMode = Literal["latest", "random"]
ACTIVITY_MODES: tuple[ActivityMode, ...] = ("latest", "random")

# The latest view changes slowly; keep it briefly so the home page does not refetch it
# on every visit. The random view is never cached: each load should be a new set.
LATEST_CACHE_TTL_SECONDS = 300

_MD5_PATTERN = re.compile(r"/md5/([0-9a-f]{32})")
_REPORT_PREFIX = "File issue:"

_latest_cache_lock = threading.Lock()
_latest_cache: tuple[float, list[ActivityGroup]] | None = None


@dataclass
class ActivityItem:
    """One comment, reaction or file report."""

    kind: Literal["comment", "reaction", "report"]
    user: str | None = None
    text: str | None = None
    report_type: str | None = None
    created_at: str | None = None  # ISO 8601, UTC
    relative_time: str | None = None  # As Anna's Archive shows it, e.g. "2 hours ago"


@dataclass
class ActivityGroup:
    """All activity on one file, newest first."""

    md5: str
    title: str | None = None
    author: str | None = None
    details: list[str] = field(default_factory=list)  # e.g. ["English [en]", "PDF", "1.2MB"]
    cover_url: str | None = None  # Only file reports carry a cover
    record_url: str | None = None  # The file's page on the Anna's Archive mirror in use
    items: list[ActivityItem] = field(default_factory=list)


def _parse_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        # Anna's Archive writes "October 18, 2026, 2:48:24 PM UTC"
        parsed = datetime.strptime(
            value.replace(" UTC", " +0000").strip(), "%B %d, %Y, %I:%M:%S %p %z"
        )
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat()


def _parse_file_block(block: Tag, group: ActivityGroup) -> None:
    """Copy the title, author, format line and cover of a report's file card into its group."""
    title_link = block.select_one("a.js-vim-focus")
    if title_link is not None and group.title is None:
        group.title = normalize_optional_text(title_link.get_text(" ", strip=True))

    # Anna's Archive leaves src empty when it knows no cover for the file.
    cover = block.find("img", src=re.compile(r"^https?://"))
    if cover is not None and group.cover_url is None:
        group.cover_url = normalize_optional_text(str(cover.get("src")))

    author_icon = block.find("span", class_=lambda c: bool(c) and "mdi--user-edit" in c)
    if author_icon is not None and author_icon.parent is not None and group.author is None:
        group.author = normalize_optional_text(author_icon.parent.get_text(" ", strip=True))

    details_div = block.find("div", class_="text-gray-800")
    if details_div is not None and not group.details:
        text = details_div.get_text(" ", strip=True).replace("\xa0", " ")
        parts = [part.strip() for part in text.split("·")]
        group.details = [part for part in parts if part and part != "Save"]


def _parse_item(block: Tag, header: Tag) -> ActivityItem:
    time_div = header.find("div", title=True)
    created_at = _parse_timestamp(str(time_div.get("title"))) if time_div else None
    relative_time = normalize_optional_text(time_div.get_text()) if time_div else None

    user_span = block.find("span", href=re.compile(r"^/profile/"))
    user = normalize_optional_text(user_span.get_text()) if user_span else None

    report_span = block.find(
        "span", class_="italic", string=lambda s: bool(s) and s.strip().startswith(_REPORT_PREFIX)
    )
    if report_span is not None:
        report_text = block.find("div", class_="border-l-4")
        return ActivityItem(
            kind="report",
            user=user,
            text=normalize_optional_text(report_text.get_text()) if report_text else None,
            report_type=normalize_optional_text(
                report_span.get_text().strip().removeprefix(_REPORT_PREFIX)
            ),
            created_at=created_at,
            relative_time=relative_time,
        )

    reaction_icon = block.find("span", class_=lambda c: bool(c) and "material-symbols" in c)
    if "Reaction" in header.get_text() and reaction_icon is not None:
        label = reaction_icon.parent.get_text(" ", strip=True) if reaction_icon.parent else ""
        # The reactor's name ends the line; strip only that copy, not text that matches it.
        if user:
            label = label.removesuffix(user)
        return ActivityItem(
            kind="reaction",
            user=user,
            text=normalize_optional_text(label),
            created_at=created_at,
            relative_time=relative_time,
        )

    comment_text = block.find("div", class_="whitespace-pre-line")
    return ActivityItem(
        kind="comment",
        user=user,
        text=normalize_optional_text(comment_text.get_text()) if comment_text else None,
        created_at=created_at,
        relative_time=relative_time,
    )


def parse_activity_page(html: str) -> list[ActivityGroup]:
    """Parse the ``/activity`` page into per-file groups, in the order the page lists them."""
    soup = BeautifulSoup(html, "html.parser")
    groups: dict[str, ActivityGroup] = {}

    for block in soup.select("div.pb-2.mb-4.border-b"):
        header = block.find("div")
        if not isinstance(header, Tag):
            continue
        # A report names its file in an HTML comment, so search the markup, not the text.
        match = _MD5_PATTERN.search(str(header))
        if match is None:
            continue

        md5 = match.group(1)
        group = groups.setdefault(md5, ActivityGroup(md5=md5))
        _parse_file_block(block, group)
        group.items.append(_parse_item(block, header))

    return list(groups.values())


def _fetch_activity_html(mode: ActivityMode) -> str:
    path = "/activity?randomize=true" if mode == "random" else "/activity"
    url = f"{network.get_aa_base_url()}{path}"
    # Optional decoration on the home page: never hold it on a browser challenge solve.
    response = downloader.html_get_page(
        url,
        selector=network.AAMirrorSelector(),
        allow_bypasser_fallback=False,
        success_delay=0,
    )
    html = html_response_text(response) if response else ""
    if not html:
        msg = "Anna's Archive activity is not reachable right now."
        raise SearchUnavailableError(msg)
    return html


def get_activity(mode: ActivityMode, *, refresh: bool = False) -> list[ActivityGroup]:
    """Return Anna's Archive activity grouped by file.

    Raises:
        SearchUnavailableError: Direct Download is off, unconfigured or unreachable.

    """
    global _latest_cache

    ensure_available()

    if mode == "latest" and not refresh:
        with _latest_cache_lock:
            if _latest_cache is not None:
                fetched_at, groups = _latest_cache
                if time.monotonic() - fetched_at < LATEST_CACHE_TTL_SECONDS:
                    return groups

    groups = parse_activity_page(_fetch_activity_html(mode))
    # Readers open this page in their own browser when the server cannot fetch it.
    base_url = network.get_aa_base_url()
    for group in groups:
        group.record_url = f"{base_url}/md5/{group.md5}"

    if mode == "latest":
        with _latest_cache_lock:
            _latest_cache = (time.monotonic(), groups)

    return groups
