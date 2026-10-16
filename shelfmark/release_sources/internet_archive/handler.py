"""Download handler for the Internet Archive release source.

A release names an item and a format. The handler reads the item's file list, picks
the best free file of that format, and streams it like any other public file.
"""

from typing import TYPE_CHECKING
from urllib.parse import quote

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.download.public_file import download_public_file
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.internet_archive.source import FORMAT_NAMES, split_source_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

METADATA_URL = "https://archive.org/metadata"
DOWNLOAD_URL = "https://archive.org/download"
_METADATA_TIMEOUT = 30


def pick_file(files: list[dict], fmt: str) -> str | None:
    """Return the name of the best free file of a format, or None.

    Private files belong to lending copies and cannot be downloaded. Within the
    format's archive.org names, the earlier name wins; an uploaded original beats a
    file archive.org derived from it.
    """
    names = FORMAT_NAMES.get(fmt, ())
    candidates = [
        file
        for file in files
        if isinstance(file, dict)
        and file.get("format") in names
        and not file.get("private")
        and file.get("name")
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda file: (names.index(file["format"]), file.get("source") != "original")
    )
    return str(candidates[0]["name"])


def _item_files(identifier: str) -> list[dict]:
    """Return the file list of an archive.org item."""
    url = f"{METADATA_URL}/{quote(identifier, safe='')}/files"
    response = requests.get(
        url, timeout=_METADATA_TIMEOUT, proxies=get_proxies(url), verify=get_ssl_verify(url)
    )
    response.raise_for_status()
    files = response.json().get("result", [])
    return files if isinstance(files, list) else []


@register_handler("internet_archive")
class InternetArchiveHandler(DownloadHandler):
    """Download the free file of one format from an archive.org item."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Resolve the item's file for the task's format and stream it into TMP_DIR."""
        parts = split_source_id(task.task_id or "")
        if parts is None:
            status_callback("error", "Not an Internet Archive release")
            return None
        identifier, fmt = parts

        status_callback("resolving", "Reading the Internet Archive file list...")
        try:
            files = _item_files(identifier)
        except (requests.RequestException, ValueError) as error:
            logger.warning("Internet Archive file list failed for %s: %s", identifier, error)
            status_callback("error", f"Could not read the item's file list: {error}")
            return None

        name = pick_file(files, fmt)
        if name is None:
            status_callback("error", f"The item has no free {fmt.upper()} file")
            return None

        url = f"{DOWNLOAD_URL}/{quote(identifier, safe='')}/{quote(name)}"
        return download_public_file(
            url, cancel_flag, progress_callback, status_callback, source_label="Internet Archive"
        )

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
