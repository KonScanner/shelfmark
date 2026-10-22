"""Project Ben-Yehuda release source.

Project Ben-Yehuda (benyehuda.org) is a free library of Hebrew literature. Its API
needs a key, which anyone can get at once and free of charge on the site. A search
asks for one file format; the file link is asked for again at download time, so a
release stays valid after the link it came with expires.
"""

import re
from typing import TYPE_CHECKING, Any, ClassVar

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.release_sources import (
    BrowseRecord,
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceUnavailableError,
    plan_query_candidates,
    register_source,
)

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

API_URL = "https://benyehuda.org/api/v1"
FILE_FORMATS = ("epub", "pdf", "docx", "odt", "txt", "html")
_DEFAULT_FORMAT = "epub"
_DEFAULT_TIMEOUT = 20

# A release id comes back from the client in a queue entry; anything else is refused.
_SOURCE_ID = re.compile(rf"benyehuda:(\d+):({'|'.join(FILE_FORMATS)})")


class BenYehudaError(Exception):
    """The API refused the request or did not answer."""


def split_source_id(source_id: str) -> tuple[str, str] | None:
    """Split a release id ``benyehuda:<text id>:<format>``, or return None."""
    match = _SOURCE_ID.fullmatch(source_id or "")
    return (match.group(1), match.group(2)) if match else None


def api_key() -> str:
    return str(config.get("BENYEHUDA_API_KEY", "") or "").strip()


def file_format() -> str:
    value = str(config.get("BENYEHUDA_FILE_FORMAT", _DEFAULT_FORMAT) or "").strip().lower()
    return value if value in FILE_FORMATS else _DEFAULT_FORMAT


def _timeout() -> int:
    return coerce_int(config.get("BENYEHUDA_TIMEOUT", _DEFAULT_TIMEOUT), _DEFAULT_TIMEOUT)


def call_api(
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the API and return its JSON object.

    Raises:
        BenYehudaError: the request failed or the answer was not a JSON object.
    """
    url = f"{API_URL}{path}"
    try:
        response = requests.request(
            method,
            url,
            params=params,
            json=body,
            headers={"Accept": "application/json"},
            timeout=_timeout(),
            proxies=get_proxies(url),
            verify=get_ssl_verify(url),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise BenYehudaError(str(error)) from error
    if not isinstance(payload, dict):
        msg = f"{url} did not answer with a JSON object"
        raise BenYehudaError(msg)
    return payload


def _metadata_text(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _text_to_release(text: dict[str, Any], fmt: str) -> Release | None:
    source_id = f"benyehuda:{text.get('id')}:{fmt}"
    raw_metadata = text.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    title = _metadata_text(metadata, "title", "sort_title")
    if split_source_id(source_id) is None or not title:
        return None
    published = _metadata_text(
        metadata, "orig_publication_date", "pby_publication_date", "raw_publication_date"
    )
    year = match.group(0) if published and (match := re.search(r"\d{4}", published)) else None
    return Release(
        source="benyehuda",
        source_id=source_id,
        title=title,
        format=fmt,
        language="he",
        download_url=None,  # Asked for again at download time.
        info_url=str(text.get("url") or "") or None,
        protocol=ReleaseProtocol.HTTP,
        indexer="Project Ben-Yehuda",
        content_type="ebook",
        extra={
            "author": _metadata_text(metadata, "author_string", "authors_string"),
            "year": year,
            "language": "he",
            "genre": _metadata_text(metadata, "genre"),
        },
    )


@register_source("benyehuda")
class BenYehudaSource(ReleaseSource):
    """Release source that searches Project Ben-Yehuda."""

    name = "benyehuda"
    display_name = "Project Ben-Yehuda"
    # The API pages with a cursor, not a page number, so a search gives one page.
    supports_browse_paging: ClassVar[bool] = False
    supported_content_types: ClassVar[list[str]] = ["ebook"]

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search the full text, titles and authors; the first query that finds anything wins.

        Raises:
            SourceUnavailableError: the API could not be searched at all.
        """
        if content_type != "ebook" or not self.is_available():
            return []
        fmt = file_format()
        for index, query in enumerate(plan_query_candidates(plan, book)):
            body = {
                "key": api_key(),
                "view": "basic",
                "file_format": fmt,
                "snippet": False,
                "fulltext": query,
            }
            try:
                payload = call_api("POST", "/search", body=body)
            except BenYehudaError as error:
                logger.warning("Project Ben-Yehuda search failed for '%s': %s", query, error)
                if index == 0:
                    msg = f"Project Ben-Yehuda search failed: {error}"
                    raise SourceUnavailableError(msg) from error
                return []
            data = payload.get("data")
            texts = data if isinstance(data, list) else []
            releases = [
                release
                for text in texts
                if isinstance(text, dict) and (release := _text_to_release(text, fmt)) is not None
            ]
            logger.info("Project Ben-Yehuda search '%s': %d release(s)", query, len(releases))
            if releases:
                return releases
        return []

    def is_available(self) -> bool:
        """Return True when the source is enabled and has an API key."""
        return config.get("BENYEHUDA_ENABLED", False) is True and bool(api_key())

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
        parts = split_source_id(record_id)
        if parts is None:
            return None
        text_id, fmt = parts
        return BrowseRecord(
            id=record_id,
            title=record_id,
            source=self.name,
            format=fmt,
            language="he",
            source_url=f"https://benyehuda.org/read/{text_id}",
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Format column for the release list (every text is in Hebrew)."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
            ],
            grid_template="minmax(0, 2fr) 80px",
            supported_filters=["format"],
        )
