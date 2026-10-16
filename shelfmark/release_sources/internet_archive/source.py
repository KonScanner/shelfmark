"""Internet Archive release source.

Uses the archive.org advanced search API. One item can carry several book formats
(an original EPUB and a derived PDF, for example). Each item becomes one release in
the first wanted format it offers; the other formats travel along as
``extra["format_variants"]`` (format -> release id), so a client can offer another
format of the same item without a new search. The exact file is picked at download
time from the item's file list, which keeps a search to one API request.
"""

import re
import unicodedata
from typing import TYPE_CHECKING, ClassVar

import requests

from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
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
)
from shelfmark.release_sources.direct_download.common import get_supported_formats

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

SEARCH_URL = "https://archive.org/advancedsearch.php"
DETAILS_URL = "https://archive.org/details"
COVER_URL = "https://archive.org/services/img"

_DEFAULT_MAX_RESULTS = 50
_DEFAULT_TIMEOUT = 20

# archive.org format names for files anyone can download, by our format name, in the
# order the handler prefers them. Encrypted lending copies ("ACS Encrypted EPUB",
# "LCP Encrypted PDF") are not listed, so they never count as a release.
FORMAT_NAMES: dict[str, tuple[str, ...]] = {
    "epub": ("EPUB",),
    "pdf": ("Text PDF", "PDF", "Additional Text PDF", "Image Container PDF"),
    "mobi": ("MOBI",),
    "djvu": ("DjVu",),
}

# Free downloads only: lending-library items hold their files back.
_ITEM_FILTER = "mediatype:texts AND NOT access-restricted-item:true"

# Characters with a meaning in the Lucene query syntax that archive.org parses.
_QUERY_SYNTAX = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')

_SEARCH_FIELDS = ("identifier", "title", "creator", "year", "language", "format", "downloads")

# archive.org item identifiers: letters, digits, '.', '-' and '_'. A release id comes
# back from the client in a queue entry, so anything else is refused.
_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]+")


def _strip_accents(text: str) -> str:
    """Return text without accents ("Σωτήρ" becomes "Σωτηρ")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize(
        "NFC", "".join(char for char in decomposed if not unicodedata.combining(char))
    )


def build_query(text: str) -> str | None:
    """Return the archive.org query for free texts that match ``text``.

    The archive.org index does not ignore accents: "Σωτήρ" finds 2 items and "Σωτηρ"
    finds 29. Scans and their OCR text often have no accents, so the query asks for
    both spellings.
    """
    words = " ".join(_QUERY_SYNTAX.sub(" ", text).split())
    if not words:
        return None
    plain = _strip_accents(words)
    terms = f"({words})" if plain == words else f"({words}) OR ({plain})"
    return f"({terms}) AND {_ITEM_FILTER}"


def _first_text(value: object) -> str | None:
    """Return the first non-empty string of a field that may be a string or a list."""
    values = value if isinstance(value, list) else [value]
    for item in values:
        text = str(item or "").strip()
        if text:
            return text
    return None


def _wanted_formats(plan: ReleaseSearchPlan) -> list[str]:
    """Return the formats to offer, in preference order: the browse filter, else settings."""
    filters = getattr(plan, "source_filters", None)
    chosen = getattr(filters, "format", None) or get_supported_formats()
    return [fmt.strip().lower() for fmt in chosen if fmt and fmt.strip()]


def _available_formats(doc: dict, wanted: list[str]) -> list[str]:
    """Return the wanted formats that the item offers as free files, in wanted order."""
    offered = doc.get("format")
    offered_names = set(offered if isinstance(offered, list) else [offered])
    return [
        fmt
        for fmt in wanted
        if fmt in FORMAT_NAMES and offered_names.intersection(FORMAT_NAMES[fmt])
    ]


def split_source_id(source_id: str) -> tuple[str, str] | None:
    """Split a release id ``<identifier>:<format>`` into its two parts, or None."""
    identifier, _, fmt = source_id.rpartition(":")
    if not _IDENTIFIER.fullmatch(identifier) or fmt not in FORMAT_NAMES:
        return None
    return identifier, fmt


def _doc_to_release(doc: dict, wanted: list[str]) -> Release | None:
    """Turn one archive.org search result into a release in its first wanted format."""
    identifier = _first_text(doc.get("identifier"))
    formats = _available_formats(doc, wanted)
    if identifier is None or not formats:
        return None
    title = _first_text(doc.get("title")) or identifier
    language_value = _first_text(doc.get("language"))
    language = normalize_language(language_value) if language_value else None
    downloads = doc.get("downloads")

    return Release(
        source="internet_archive",
        source_id=f"{identifier}:{formats[0]}",
        title=title,
        format=formats[0],
        language=language,
        download_url=None,  # The handler picks the file from the item's file list.
        info_url=f"{DETAILS_URL}/{identifier}",
        protocol=ReleaseProtocol.HTTP,
        indexer="Internet Archive",
        content_type="ebook",
        extra={
            "author": _first_text(doc.get("creator")),
            "year": _first_text(doc.get("year")),
            "language": language,
            "preview": f"{COVER_URL}/{identifier}",
            "downloads": downloads if isinstance(downloads, int) else None,
            "identifier": identifier,
            "format_variants": {fmt: f"{identifier}:{fmt}" for fmt in formats},
        },
    )


def _search_docs(query: str, rows: int, timeout: int, page: int = 1) -> list[dict]:
    """Run one advanced search and return its result documents."""
    params: list[tuple[str, str]] = [
        ("q", query),
        ("rows", str(rows)),
        ("page", str(page)),
        ("output", "json"),
        *(("fl[]", field) for field in _SEARCH_FIELDS),
    ]
    response = requests.get(
        SEARCH_URL,
        params=params,
        timeout=timeout,
        proxies=get_proxies(SEARCH_URL),
        verify=get_ssl_verify(SEARCH_URL),
    )
    response.raise_for_status()
    docs = response.json().get("response", {}).get("docs", [])
    return [doc for doc in docs if isinstance(doc, dict)] if isinstance(docs, list) else []


@register_source("internet_archive")
class InternetArchiveSource(ReleaseSource):
    """Release source that searches archive.org for free texts."""

    name = "internet_archive"
    display_name = "Internet Archive"
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
        """Search archive.org for free texts that match the book.

        The first search text that finds anything wins: the title + author text is
        tried before the looser title-only text.

        Raises:
            SourceUnavailableError: archive.org could not be searched at all, so the
                caller reports a failure rather than an empty result.
        """
        if content_type != "ebook" or not self.is_available():
            return []

        rows = coerce_int(
            config.get("INTERNET_ARCHIVE_MAX_RESULTS", _DEFAULT_MAX_RESULTS), _DEFAULT_MAX_RESULTS
        )
        timeout = coerce_int(
            config.get("INTERNET_ARCHIVE_TIMEOUT", _DEFAULT_TIMEOUT), _DEFAULT_TIMEOUT
        )

        page = browse_page(plan)
        wanted = _wanted_formats(plan)
        answered = False
        last_error: Exception | None = None
        for text in plan_query_candidates(plan, book):
            query = build_query(text)
            if query is None:
                continue
            try:
                docs = _search_docs(query, rows, timeout, page)
            except (requests.RequestException, ValueError) as error:
                logger.warning("Internet Archive search failed for '%s': %s", text, error)
                last_error = error
                continue
            answered = True

            releases = [
                release for doc in docs if (release := _doc_to_release(doc, wanted)) is not None
            ]
            logger.info(
                "Internet Archive search '%s' returned %d release(s) from %d item(s)",
                text,
                len(releases),
                len(docs),
            )
            if releases:
                return releases
        if last_error is not None and not answered:
            msg = f"Internet Archive search failed: {last_error}"
            raise SourceUnavailableError(msg) from last_error
        return []

    def is_available(self) -> bool:
        """Return True when the source is enabled."""
        return config.get("INTERNET_ARCHIVE_ENABLED", False) is True

    def search_results_are_releases(self) -> bool:
        """Each search result is a concrete, downloadable file of one format."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Rebuild a browse record from its id, without a network round trip."""
        parts = split_source_id(record_id)
        if parts is None:
            return None
        identifier, fmt = parts
        return BrowseRecord(
            id=record_id,
            title=identifier,
            source=self.name,
            format=fmt,
            preview=f"{COVER_URL}/{identifier}",
            source_url=f"{DETAILS_URL}/{identifier}",
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language, format and download-count columns for the release list."""
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
                    key="extra.downloads",
                    label="Downloads",
                    render_type=ColumnRenderType.NUMBER,
                    align=ColumnAlign.CENTER,
                    width="90px",
                ),
            ],
            grid_template="minmax(0, 2fr) 60px 80px 90px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.THUMBNAIL, key="extra.preview"),
            supported_filters=["format", "language"],
        )
