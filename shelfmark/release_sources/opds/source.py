"""OPDS release source.

OPDS is the Atom feed format that book catalogues publish for e-reader apps: Project
Gutenberg, Flibusta, Calibre-Web, Kavita, Komga, COPS and many more. One configured
feed URL is either a search template with ``{searchTerms}`` or a catalogue root; for a
root, the search link is read from the catalogue itself.

A search entry usually carries its file links (acquisition links). Some catalogues,
Project Gutenberg among them, list each book as a link to its own small feed instead;
those feeds are read too, and only entries that turn out to hold files become releases.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

import requests
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.core.utils import fold_text, query_words
from shelfmark.download import http as downloader
from shelfmark.download.network import get_proxies, get_ssl_verify, prepare_public_url
from shelfmark.download.public_file import url_origin
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
    from xml.etree.ElementTree import Element

    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

DEFAULT_FEEDS = ["https://www.gutenberg.org/ebooks/search.opds/?query={searchTerms}"]

_DEFAULT_MAX_RESULTS = 25
_DEFAULT_TIMEOUT = 20
# Book feeds read at once when a catalogue lists books as links to their own feeds.
_RESOLVE_WORKERS = 6
# Shortest query word that counts as a match ("the" and "of" match nearly any title).
_MIN_MATCH_WORD = 4
_CACHE_SECONDS = 3600.0
_CACHE_MAX_ENTRIES = 1000

_ATOM = "{http://www.w3.org/2005/Atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
_ACQUISITION = "http://opds-spec.org/acquisition"
_THUMBNAIL_RELS = ("http://opds-spec.org/image/thumbnail", "http://opds-spec.org/image")
_TEXT_TAGS = {
    "language": (
        "{http://purl.org/dc/terms/}language",
        "{http://purl.org/dc/elements/1.1/}language",
    ),
    "issued": (
        "{http://purl.org/dc/terms/}issued",
        "{http://purl.org/dc/elements/1.1/}issued",
        f"{_ATOM}published",
    ),
}

# File MIME types, by format. A "+zip" suffix (Flibusta's "application/fb2+zip") means
# the same format in a ZIP archive; the download step unpacks archives.
_MIME_FORMATS = {
    "application/epub+zip": "epub",
    "application/x-mobipocket-ebook": "mobi",
    "application/x-mobi8-ebook": "azw3",
    "application/vnd.amazon.ebook": "azw3",
    "application/pdf": "pdf",
    "application/fb2": "fb2",
    "application/x-fictionbook+xml": "fb2",
    "text/fb2+xml": "fb2",
    "image/vnd.djvu": "djvu",
    "image/x-djvu": "djvu",
    "application/x-djvu": "djvu",
    "application/vnd.comicbook+zip": "cbz",
    "application/x-cbz": "cbz",
    "application/vnd.comicbook-rar": "cbr",
    "application/x-cbr": "cbr",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/txt": "txt",
    "text/plain": "txt",
    "application/vnd.oasis.opendocument.text": "odt",
}
_NO_IMAGES = re.compile(r"no\s*images|noimages", re.IGNORECASE)
_TEMPLATE_PARAM = re.compile(r"\{(?:\w+:)?(\w+)\??\}")


class _FeedError(Exception):
    """A feed could not be searched (no search link, bad XML, HTTP failure)."""


@dataclass(frozen=True)
class Feed:
    """One configured catalogue: its URL without credentials, and its login."""

    url: str
    auth: tuple[str, str] | None
    origin: str
    label: str


@dataclass
class _Entry:
    title: str
    authors: list[str] = field(default_factory=list)
    language: str | None = None
    year: str | None = None
    preview: str | None = None
    info_url: str | None = None
    # (format, absolute URL, size in bytes), in feed order.
    files: list[tuple[str, str, int | None]] = field(default_factory=list)
    book_feed: str | None = None


def _split_credentials(url: str) -> tuple[str, tuple[str, str] | None]:
    """Return a URL without ``user:pass@`` and the credentials it carried."""
    parts = urlparse(url)
    if parts.username is None:
        return url, None
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    clean = urlunparse(parts._replace(netloc=netloc))
    return clean, (unquote(parts.username), unquote(parts.password or ""))


def _feed_label(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host.removeprefix("www.")


def configured_feeds() -> list[Feed]:
    """Return the configured feeds; a blank setting means the defaults."""
    raw = config.get("OPDS_FEEDS", DEFAULT_FEEDS)
    values = raw.split(",") if isinstance(raw, str) else raw
    feeds: list[Feed] = []
    for value in values if isinstance(values, list | tuple) else []:
        text = str(value or "").strip()
        if not text.startswith(("http://", "https://")):
            continue
        url, auth = _split_credentials(text)
        feeds.append(Feed(url=url, auth=auth, origin=url_origin(url), label=_feed_label(url)))
    return feeds


def trusted_origins() -> dict[str, tuple[str, str] | None]:
    """Return each configured feed's origin and its login, for the download step."""
    return {feed.origin: feed.auth for feed in configured_feeds()}


def _local_text(element: Element, tags: tuple[str, ...]) -> str | None:
    for tag in tags:
        found = element.find(tag)
        if found is not None and found.text and found.text.strip():
            return found.text.strip()
    return None


def _link_format(link: Element) -> str | None:
    mime = (link.get("type") or "").split(";", 1)[0].strip().lower()
    if mime in _MIME_FORMATS:
        return _MIME_FORMATS[mime]
    if mime.endswith("+zip") and mime.removesuffix("+zip") in _MIME_FORMATS:
        return _MIME_FORMATS[mime.removesuffix("+zip")]
    # Some catalogues send a generic type; the path then names the format ("/b/1/fb2").
    tail = urlparse(link.get("href") or "").path.rsplit("/", 1)[-1].lower()
    extension = tail.rsplit(".", 1)[-1]
    return extension if extension in set(_MIME_FORMATS.values()) else None


def _parse_entry(element: Element, base_url: str) -> _Entry:
    entry = _Entry(title=_local_text(element, (f"{_ATOM}title",)) or "")
    for author in element.findall(f"{_ATOM}author"):
        name = _local_text(author, (f"{_ATOM}name",))
        if name:
            entry.authors.append(name)
    entry.language = _local_text(element, _TEXT_TAGS["language"])
    issued = _local_text(element, _TEXT_TAGS["issued"])
    entry.year = issued[:4] if issued and issued[:4].isdigit() else None

    for link in element.findall(f"{_ATOM}link"):
        href = link.get("href") or ""
        rel = link.get("rel") or ""
        if not href or href.startswith("data:"):
            continue
        url = urljoin(base_url, href)
        mime = link.get("type") or ""
        if rel.startswith(_ACQUISITION):
            fmt = _link_format(link)
            if fmt:
                entry.files.append((fmt, url, coerce_int(link.get("length"), 0) or None))
        elif rel in _THUMBNAIL_RELS and (entry.preview is None or rel == _THUMBNAIL_RELS[0]):
            entry.preview = url
        elif rel == "alternate" and "html" in mime:
            entry.info_url = url
        elif "opds-catalog" in mime and rel in {"subsection", ""}:
            entry.book_feed = url
    return entry


def _format_size(length: int | None) -> str | None:
    if not length:
        return None
    size = float(length)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in {"B", "KB"} else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _best_files(entry: _Entry, wanted: list[str]) -> dict[str, tuple[str, int | None]]:
    """Return one file per wanted format, in wanted order.

    Within a format, a file with images beats a "no images" copy (Gutenberg offers both).
    """
    best: dict[str, tuple[str, int | None]] = {}
    for fmt in wanted:
        choices = [(url, size) for file_fmt, url, size in entry.files if file_fmt == fmt]
        if not choices:
            continue
        with_images = [choice for choice in choices if not _NO_IMAGES.search(choice[0])]
        best[fmt] = (with_images or choices)[0]
    return best


def _matches(entry: _Entry, words: set[str]) -> bool:
    """Whether the entry's title or authors contain a query word.

    Some catalogues answer a query they cannot match with unrelated books (Flibusta
    does for non-Russian text); those entries are dropped.
    """
    haystack = fold_text(" ".join([entry.title, *entry.authors]))
    return not words or any(word in haystack for word in words)


def _to_release(entry: _Entry, feed: Feed, wanted: list[str]) -> Release | None:
    files = _best_files(entry, wanted)
    if not files or not entry.title:
        return None
    fmt, (url, size) = next(iter(files.items()))
    language = normalize_language(entry.language) if entry.language else None
    return Release(
        source="opds",
        source_id=url,
        title=entry.title,
        format=fmt,
        language=language,
        size=_format_size(size),
        download_url=url,
        info_url=entry.info_url or url,
        protocol=ReleaseProtocol.HTTP,
        indexer=feed.label,
        content_type="ebook",
        extra={
            "author": ", ".join(entry.authors) or None,
            "year": entry.year,
            "language": language,
            "preview": entry.preview,
            "format_variants": {variant: file[0] for variant, file in files.items()},
        },
    )


class _FeedReader:
    """Fetch and parse the documents of one feed, with its login on its own origin."""

    def __init__(self, feed: Feed, timeout: int) -> None:
        self.feed = feed
        self.timeout = timeout

    def get(self, url: str) -> bytes:
        auth = None
        if url_origin(url) == self.feed.origin:
            auth = self.feed.auth
        else:
            # Links in a feed can point anywhere; only the configured origin may be private.
            checked = prepare_public_url(url)
            if checked is None:
                msg = f"refused non-public link {url}"
                raise _FeedError(msg)
            url = checked
        headers = {
            "User-Agent": downloader.DOWNLOAD_HEADERS["User-Agent"],
            "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.5",
        }
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                auth=auth,
                proxies=get_proxies(url),
                verify=get_ssl_verify(url),
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise _FeedError(str(error)) from error
        return response.content

    def xml(self, url: str) -> Element:
        try:
            return DefusedElementTree.fromstring(self.get(url))
        except (DefusedElementTree.ParseError, DefusedXmlException) as error:
            msg = f"not an XML feed: {url}"
            raise _FeedError(msg) from error


_template_cache: dict[str, tuple[float, str]] = {}
_book_feed_cache: dict[str, tuple[float, _Entry | None]] = {}
_cache_lock = threading.Lock()


def _cache_get[T](cache: dict[str, tuple[float, T]], key: str) -> tuple[bool, T | None]:
    with _cache_lock:
        hit = cache.get(key)
    if hit is None or time.monotonic() - hit[0] > _CACHE_SECONDS:
        return False, None
    return True, hit[1]


def _cache_put[T](cache: dict[str, tuple[float, T]], key: str, value: T) -> None:
    with _cache_lock:
        if len(cache) >= _CACHE_MAX_ENTRIES:
            cache.clear()
        cache[key] = (time.monotonic(), value)


def _search_template(reader: _FeedReader) -> str:
    """Return the feed's search URL template, reading the catalogue when it is a root."""
    url = reader.feed.url
    if "{searchTerms}" in url:
        return url
    found, cached = _cache_get(_template_cache, url)
    if found and cached:
        return cached

    root = reader.xml(url)
    atom_template = None
    description_url = None
    for link in root.findall(f"{_ATOM}link"):
        if link.get("rel") != "search" or not link.get("href"):
            continue
        href = urljoin(url, link.get("href") or "")
        if "atom" in (link.get("type") or "") and "{searchTerms}" in href:
            atom_template = atom_template or href
        if "opensearchdescription" in (link.get("type") or ""):
            description_url = description_url or href
    # The OpenSearch description first: a catalogue's own atom search link can lead to
    # a menu ("books or authors?") rather than to books, as Flibusta's does.
    template = atom_template
    if description_url:
        description = reader.xml(description_url)
        for entry in description.findall(f"{_OPENSEARCH}Url"):
            candidate = entry.get("template") or ""
            if "atom" in (entry.get("type") or "") and "{searchTerms}" in candidate:
                template = urljoin(description_url, candidate)
                break
    if template:
        template = _keep_scheme(template, reader.feed)
        _cache_put(_template_cache, url, template)
        return template
    msg = f"no search link in catalogue {url}"
    raise _FeedError(msg)


def _keep_scheme(template: str, feed: Feed) -> str:
    """Keep an https feed on https when its catalogue links its search over plain http.

    Gutenberg's and Flibusta's search descriptions both name http URLs; following them
    would send every query unencrypted.
    """
    parts = urlparse(template)
    if parts.scheme == "http" and feed.url.startswith("https://"):
        return urlunparse(parts._replace(scheme="https"))
    return template


def _fill_template(template: str, query: str, count: int) -> str:
    """Fill an OpenSearch template for the first page.

    Optional values ("{startPage?}") stay empty, so the catalogue applies its own first
    page; later pages follow the feed's "next" links.
    """
    values = {"searchTerms": quote(query), "count": str(count), "startPage": "1", "startIndex": "1"}

    def fill(match: re.Match[str]) -> str:
        if match.group(0).endswith("?}") and match.group(1) != "searchTerms":
            return ""
        return values.get(match.group(1), "")

    return _TEMPLATE_PARAM.sub(fill, template)


def _next_link(root: Element, base_url: str) -> str | None:
    for link in root.findall(f"{_ATOM}link"):
        if link.get("rel") == "next" and link.get("href"):
            return urljoin(base_url, link.get("href") or "")
    return None


def _read_book_feed(reader: _FeedReader, url: str) -> _Entry | None:
    """Return the entry with files from a book's own feed, or None (a navigation feed)."""
    found, cached = _cache_get(_book_feed_cache, url)
    if found:
        return cached
    try:
        root = reader.xml(url)
    except _FeedError as error:
        logger.debug("OPDS book feed %s failed: %s", url, error)
        return None
    with_files = [
        parsed
        for element in root.findall(f"{_ATOM}entry")
        if (parsed := _parse_entry(element, url)).files
    ]
    entry = with_files[0] if with_files else None
    if entry is not None:
        # One book can span several entries (Gutenberg lists its "no images" files apart).
        for other in with_files[1:]:
            if other.title == entry.title:
                entry.files.extend(other.files)
                entry.preview = entry.preview or other.preview
    _cache_put(_book_feed_cache, url, entry)
    return entry


def _merge(listed: _Entry, resolved: _Entry | None) -> _Entry | None:
    if resolved is None:
        return None
    resolved.title = resolved.title or listed.title
    resolved.authors = resolved.authors or listed.authors
    resolved.preview = resolved.preview or listed.preview
    resolved.info_url = resolved.info_url or listed.info_url or listed.book_feed
    return resolved


def search_feed(reader: _FeedReader, query: str, page: int, max_results: int) -> list[_Entry]:
    """Return the entries with files for one query and page of one feed."""
    url = _fill_template(_search_template(reader), query, max_results)
    root = reader.xml(url)
    for _ in range(page - 1):
        next_url = _next_link(root, url)
        if next_url is None:
            return []
        url = next_url
        root = reader.xml(url)

    listed = [_parse_entry(element, url) for element in root.findall(f"{_ATOM}entry")]
    listed = listed[:max_results]
    to_resolve = [entry for entry in listed if not entry.files and entry.book_feed]
    resolved: dict[int, _Entry | None] = {}
    if to_resolve:
        with ThreadPoolExecutor(max_workers=min(_RESOLVE_WORKERS, len(to_resolve))) as executor:
            books = executor.map(lambda e: _read_book_feed(reader, e.book_feed or ""), to_resolve)
            for entry, book in zip(to_resolve, books, strict=True):
                resolved[id(entry)] = _merge(entry, book)

    entries: list[_Entry] = []
    for entry in listed:
        if entry.files:
            entries.append(entry)
        elif (book := resolved.get(id(entry))) is not None:
            entries.append(book)
    return entries


@register_source("opds")
class OPDSSource(ReleaseSource):
    """Release source that searches OPDS catalogues."""

    name = "opds"
    display_name = "OPDS Catalogs"
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
        """Search every configured feed at once and merge their releases.

        In each feed, the first query text that finds anything wins.

        Raises:
            SourceUnavailableError: no feed could be searched at all.
        """
        if content_type != "ebook" or not self.is_available():
            return []
        queries = plan_query_candidates(plan, book)
        if not queries:
            return []
        max_results = coerce_int(
            config.get("OPDS_MAX_RESULTS", _DEFAULT_MAX_RESULTS), _DEFAULT_MAX_RESULTS
        )
        timeout = coerce_int(config.get("OPDS_TIMEOUT", _DEFAULT_TIMEOUT), _DEFAULT_TIMEOUT)
        page = browse_page(plan)
        wanted = wanted_formats(plan)
        feeds = configured_feeds()

        def search_one(feed: Feed) -> list[Release] | None:
            reader = _FeedReader(feed, timeout)
            try:
                for query in queries:
                    entries = search_feed(reader, query, page, max_results)
                    words = query_words([query], min_length=_MIN_MATCH_WORD)
                    releases = [
                        release
                        for entry in entries
                        if _matches(entry, words)
                        and (release := _to_release(entry, feed, wanted)) is not None
                    ]
                    if releases:
                        logger.info(
                            "OPDS %s: '%s' page %d gave %d release(s)",
                            feed.label,
                            query,
                            page,
                            len(releases),
                        )
                        return releases
            except _FeedError as error:
                logger.warning("OPDS search failed on %s: %s", feed.label, error)
                return None
            return []

        with ThreadPoolExecutor(max_workers=len(feeds)) as executor:
            answers = list(executor.map(search_one, feeds))
        if answers and all(answer is None for answer in answers):
            msg = f"No OPDS catalog answered: {', '.join(feed.label for feed in feeds)}"
            raise SourceUnavailableError(msg)
        return [release for answer in answers if answer for release in answer]

    def is_available(self) -> bool:
        """Return True when the source is enabled and has at least one feed."""
        return config.get("OPDS_ENABLED", False) is True and bool(configured_feeds())

    def search_results_are_releases(self) -> bool:
        """Each search result is one downloadable file."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Rebuild a browse record from its file URL, without a network round trip."""
        if not record_id.startswith(("http://", "https://")):
            return None
        name = unquote(urlparse(record_id).path.rsplit("/", 1)[-1]) or record_id
        return BrowseRecord(id=record_id, title=name, source=self.name, source_url=record_id)

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
