"""SearXNG release source.

Queries a self-hosted SearXNG JSON API and keeps only results whose URL links
directly to a book file (by extension), so every release is downloadable over
plain HTTP.
"""

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
    register_source,
)
from shelfmark.release_sources.direct_download.common import get_supported_formats

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_FALLBACK_EXTENSIONS = {"pdf", "epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"}
_MIN_TITLE_LENGTH = 3


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


def _build_query_candidates(book: BookMetadata, plan: ReleaseSearchPlan) -> list[str]:
    """Return ordered, deduplicated queries for the SearXNG search."""
    candidates: list[str] = []
    if plan.manual_query and plan.manual_query.strip():
        candidates.append(plan.manual_query.strip())
    elif plan.title_variants:
        variant = plan.title_variants[0]
        combined = " ".join(part for part in [variant.title, variant.author] if part).strip()
        title_only = (variant.title or "").strip()
        if combined:
            candidates.append(combined)
        if title_only and title_only.lower() != combined.lower():
            candidates.append(title_only)
    elif book.title and book.title.strip():
        candidates.append(book.title.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _fetch_results(query: str, timeout: int) -> list[dict]:
    """Query the SearXNG JSON API and return its raw result list."""
    params: dict[str, str] = {"q": query, "format": "json"}

    categories = _split_csv(config.get("SEARXNG_CATEGORIES", "general"))
    if categories:
        params["categories"] = ",".join(categories)
    engines = _split_csv(config.get("SEARXNG_ENGINES", ""))
    if engines:
        params["engines"] = ",".join(engines)
    language = str(config.get("SEARXNG_LANGUAGE", "") or "").strip()
    if language and language.lower() != "all":
        params["language"] = language

    response = requests.get(f"{_base_url()}/search", params=params, timeout=timeout)
    if response.status_code == 403:
        logger.warning("SearXNG refused JSON output. Enable the json format on the instance.")
        return []
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", [])
    return results if isinstance(results, list) else []


def _to_release(result: dict) -> Release | None:
    """Turn a SearXNG result into a Release when it links to a book file."""
    url = str(result.get("url") or "").strip()
    extension = _url_extension(url) if url else None
    if not url or not extension or extension not in _file_extensions():
        return None

    title = str(result.get("title") or "").strip() or _filename_from_url(url)
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

        """
        if content_type != "ebook":
            return []
        if not self.is_available():
            return []

        timeout = _coerce_int(config.get("SEARXNG_TIMEOUT", 20), 20)
        max_results = _coerce_int(config.get("SEARXNG_MAX_RESULTS", 30), 30)
        extensions = _file_extensions()

        for query in _build_query_candidates(book, plan):
            try:
                raw_results = _fetch_results(query, timeout)
            except requests.RequestException as error:
                logger.warning("SearXNG search failed for '%s': %s", query, error)
                continue

            releases: list[Release] = []
            seen_urls: set[str] = set()
            for raw in raw_results:
                if len(releases) >= max_results:
                    break
                if not isinstance(raw, dict):
                    continue
                release = _to_release(raw)
                if release is None or release.source_id in seen_urls:
                    continue
                seen_urls.add(release.source_id)
                releases.append(release)

            if releases:
                logger.info(
                    "SearXNG search '%s' returned %d file link(s) out of %d result(s)",
                    query,
                    len(releases),
                    len(raw_results),
                )
                return releases
            logger.info(
                "SearXNG search '%s' returned no %s file links", query, ",".join(sorted(extensions))
            )

        return []

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
