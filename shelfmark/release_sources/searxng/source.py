"""SearXNG release source.

Queries a self-hosted SearXNG JSON API and keeps only results whose URL links
directly to a book file (by extension), so every release is downloadable over
plain HTTP.

A bare query rarely surfaces file links, so each search fans out into several
requests (file-type operators and file-finding engines) and merges the results.
"""

import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import unquote, urlparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
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

_FALLBACK_EXTENSIONS = {"pdf", "epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"}
_MIN_TITLE_LENGTH = 3

# Operators added to each query for the general engines. Engines return few file
# links for a bare query; `filetype:pdf` and `ext:epub` find far more. An OR of
# several operators makes the engines return nothing, so each one is its own request.
_QUERY_OPERATORS = ("", "filetype:pdf", "ext:epub")
_DEFAULT_FILE_ENGINES = "findfiles,wikicommons.files"


def _file_extensions() -> set[str]:
    """Return the extensions a result URL may carry to count as a release."""
    configured = {fmt.strip().lower() for fmt in get_supported_formats() if fmt}
    return configured | _FALLBACK_EXTENSIONS if configured else set(_FALLBACK_EXTENSIONS)


def _url_extension(url: str) -> str | None:
    """Return the lowercase extension of a URL path, or None."""
    path = urlparse(url).path
    name = unquote(path).rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return name.rsplit(".", 1)[-1].lower()


def _direct_file_url(url: str) -> str:
    """Rewrite a known file-page URL to the URL that serves the file itself.

    Wikimedia Commons and GitHub link to an HTML page about the file. Both have a
    stable URL form that returns (or redirects to) the raw bytes.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host == "commons.wikimedia.org" and parsed.path.startswith("/wiki/File:"):
        name = parsed.path.removeprefix("/wiki/File:")
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}"
    if host in {"github.com", "www.github.com"}:
        parts = parsed.path.split("/")
        # /<owner>/<repo>/blob/<ref>/<path...>
        if len(parts) > 4 and parts[3] == "blob":
            parts[3] = "raw"
            return f"https://github.com{'/'.join(parts)}"
    return url


def _filename_from_url(url: str) -> str:
    """Return a readable filename for a URL, without its extension."""
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name or url


def _base_url() -> str:
    """Return the configured SearXNG base URL without a trailing slash."""
    return str(config.get("SEARXNG_BASE_URL", "") or "").strip().rstrip("/")


def _split_csv(value: object) -> list[str]:
    """Split a comma-separated config value into clean entries."""
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _coerce_int(value: object, default: int) -> int:
    """Return an int from loose config storage, or the default."""
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return default


def _build_requests(queries: list[str], page: int = 1) -> list[dict[str, str]]:
    """Return the SearXNG request parameters for a search, in priority order."""
    base: dict[str, str] = {"format": "json"}
    if page > 1:
        base["pageno"] = str(page)
    categories = _split_csv(config.get("SEARXNG_CATEGORIES", "general"))
    engines = _split_csv(config.get("SEARXNG_ENGINES", ""))
    if categories:
        base["categories"] = ",".join(categories)
    if engines:
        base["engines"] = ",".join(engines)
    language = str(config.get("SEARXNG_LANGUAGE", "") or "").strip()
    if language and language.lower() != "all":
        base["language"] = language

    file_engines = _split_csv(config.get("SEARXNG_FILE_ENGINES", _DEFAULT_FILE_ENGINES))

    requests_params: list[dict[str, str]] = []
    for query in queries:
        for operator in _QUERY_OPERATORS:
            q = f"{query} {operator}".strip()
            requests_params.append({**base, "q": q})
        if file_engines:
            file_params = {key: value for key, value in base.items() if key != "categories"}
            file_params.update({"q": query, "engines": ",".join(file_engines)})
            requests_params.append(file_params)
    return requests_params


def _fetch_results(params: dict[str, str], timeout: int) -> list[dict]:
    """Query the SearXNG JSON API and return its raw result list."""
    response = requests.get(f"{_base_url()}/search", params=params, timeout=timeout)
    if response.status_code == 403:
        logger.warning("SearXNG refused JSON output. Enable the json format on the instance.")
        return []
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", [])
    return results if isinstance(results, list) else []


def _fetch_results_safe(params: dict[str, str], timeout: int) -> list[dict] | None:
    """Return the results for one request, or None when the request failed."""
    try:
        return _fetch_results(params, timeout)
    except (requests.RequestException, ValueError) as error:
        logger.warning("SearXNG search failed for '%s': %s", params.get("q"), error)
        return None


def _dedupe_key(url: str) -> str:
    """Return a key that treats http/https and a www. prefix as the same link."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return f"{host}{parsed.path}?{parsed.query}"


def _fold(text: str) -> str:
    """Return text in lower case with accents removed, for loose word matching."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _query_words(queries: list[str]) -> set[str]:
    """Return the folded words of the search queries that a match can contain."""
    words = {word for query in queries for word in _fold(query).split()}
    significant = {word for word in words if len(word) > 1}
    return significant or words


def _match_score(release: Release, words: set[str]) -> int:
    """Count the query words that appear in a release title or URL."""
    if not words:
        return 0
    haystack = _fold(f"{release.title} {unquote(release.download_url or '')}")
    return sum(1 for word in words if word in haystack)


def _interleave(result_lists: list[list[dict]]) -> list[dict]:
    """Merge result lists round-robin, so each request contributes its best hits."""
    merged: list[dict] = []
    longest = max((len(results) for results in result_lists), default=0)
    for index in range(longest):
        merged.extend(results[index] for results in result_lists if index < len(results))
    return merged


def _to_release(result: dict, extensions: set[str]) -> Release | None:
    """Turn a SearXNG result into a Release when it links to a book file."""
    url = _direct_file_url(str(result.get("url") or "").strip())
    extension = _url_extension(url) if url else None
    if not url or not extension or extension not in extensions:
        return None

    title = str(result.get("title") or "").strip().removeprefix("File:")
    title = title or _filename_from_url(url)
    if len(title) < _MIN_TITLE_LENGTH:
        title = _filename_from_url(url)

    engines = result.get("engines")
    engine = (
        engines[0] if isinstance(engines, list) and engines else str(result.get("engine") or "")
    )

    return Release(
        source="searxng",
        source_id=url,
        title=title,
        format=extension,
        download_url=url,
        info_url=url,
        protocol=ReleaseProtocol.HTTP,
        indexer="SearXNG",
        extra={
            "engine": engine or None,
            "snippet": str(result.get("content") or "").strip() or None,
        },
    )


@register_source("searxng")
class SearXNGSource(ReleaseSource):
    """Release source that searches a self-hosted SearXNG instance."""

    name = "searxng"
    display_name = "SearXNG"
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
        """Search SearXNG for direct file links matching the book.

        Args:
            book: Book metadata
            plan: Search plan with query variants
            expand_search: Ignored (SearXNG already searches loosely)
            content_type: Must be "ebook" for this source

        Returns:
            List of Release objects whose URL points at a book file.

        Raises:
            SourceUnavailableError: no request reached SearXNG, so the caller reports a
                failure rather than an empty result.

        """
        if content_type != "ebook":
            return []
        if not self.is_available():
            return []

        timeout = _coerce_int(config.get("SEARXNG_TIMEOUT", 20), 20)
        max_results = _coerce_int(config.get("SEARXNG_MAX_RESULTS", 50), 50)
        queries = plan_query_candidates(plan, book)
        if not queries:
            return []
        page = browse_page(plan)
        requests_params = _build_requests(queries, page)

        with ThreadPoolExecutor(max_workers=len(requests_params)) as executor:
            answers = list(
                executor.map(lambda params: _fetch_results_safe(params, timeout), requests_params)
            )
        result_lists = [answer for answer in answers if answer is not None]
        if not result_lists:
            # Every request failed: report it, so the caller does not show "no results".
            msg = f"SearXNG at {_base_url()} did not answer any of {len(answers)} request(s)"
            raise SourceUnavailableError(msg)

        extensions = _file_extensions()
        releases: list[Release] = []
        seen_urls: set[str] = set()
        merged = _interleave(result_lists)
        for raw in merged:
            if not isinstance(raw, dict):
                continue
            release = _to_release(raw, extensions)
            if release is None:
                continue
            key = _dedupe_key(release.source_id)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            releases.append(release)

        # Engines rank by their own idea of relevance, and file engines match loosely.
        # Put the links that name more of the query first; the sort is stable.
        words = _query_words(queries)
        releases.sort(key=lambda release: _match_score(release, words), reverse=True)
        releases = releases[:max_results]

        logger.info(
            "SearXNG search %s returned %d file link(s) out of %d result(s) from %d request(s)",
            queries,
            len(releases),
            len(merged),
            len(requests_params),
        )
        return releases

    def is_available(self) -> bool:
        """Return True when the source is enabled and a base URL is set."""
        return bool(config.get("SEARXNG_ENABLED", False)) and bool(_base_url())

    def search_results_are_releases(self) -> bool:
        """SearXNG results already represent concrete downloadable releases."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Rebuild a browse record from its URL, without a network round trip."""
        if not record_id.startswith(("http://", "https://")):
            return None
        extension = _url_extension(record_id)
        if extension is None or extension not in _file_extensions():
            return None
        return BrowseRecord(
            id=record_id,
            title=_filename_from_url(record_id),
            source=self.name,
            format=extension,
            download_urls=[record_id],
            source_url=record_id,
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column layout for the release list UI."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.engine",
                    label="Engine",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="100px",
                    color_hint=ColumnColorHint(type="static", value="bg-cyan-500/20 text-cyan-400"),
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
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
            grid_template="minmax(0, 2fr) 100px 80px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            supported_filters=["format"],
        )
