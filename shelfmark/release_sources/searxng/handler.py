"""Download handler for the SearXNG release source.

Streams the file behind a result URL into TMP_DIR and hands the path to the
orchestrator, which extracts archives and moves the payload to its destination.
"""

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify, prepare_public_url
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.searxng.source import _url_extension

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

_MIN_FILE_BYTES = 1024
_CHUNK_SIZE = 64 * 1024
_MAX_REDIRECTS = 5


def _open_public_stream(url: str) -> requests.Response | None:
    """Open a streamed GET to a public host, re-checking every redirect hop.

    The URL comes from the queued release, which a client supplies, so it must never
    reach the container's own network (SearXNG, download clients, cloud metadata).
    Requests go through the configured proxy like every other external fetch.
    """
    current = prepare_public_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        if current is None:
            return None
        response = requests.get(
            current,
            stream=True,
            timeout=(10, 60),
            allow_redirects=False,
            proxies=get_proxies(current),
            verify=get_ssl_verify(current),
        )
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        if not location:
            return None
        current = prepare_public_url(urljoin(current, location))
    return None


@register_handler("searxng")
class SearXNGHandler(DownloadHandler):
    """Download a SearXNG result URL over plain HTTP(S)."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Stream the release URL into TMP_DIR and return the staged path."""
        url = (task.source_url or task.task_id or "").strip()
        if not url.startswith(("http://", "https://")):
            status_callback("error", "No usable download URL on the task")
            return None

        extension = _url_extension(url)
        suffix = f".{extension}" if extension else ".bin"

        try:
            status_callback("resolving", "Connecting to the file host...")
            if cancel_flag.is_set():
                return None

            response = _open_public_stream(url)
            if response is None:
                logger.warning("SearXNG download blocked for non-public URL: %s", url)
                status_callback("error", "Download URL is not a public file host")
                return None
            response.raise_for_status()

            if cancel_flag.is_set():
                response.close()
                return None

            status_callback("downloading", None)
            Path(TMP_DIR).mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="searxng_", suffix=suffix, dir=TMP_DIR)
            temp_path = Path(temp_name)
            downloaded = 0

            try:
                with os.fdopen(fd, "wb") as handle:
                    total = int(response.headers.get("content-length", 0) or 0)
                    progress_callback(0.0)
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if cancel_flag.is_set():
                            temp_path.unlink(missing_ok=True)
                            return None
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            progress_callback(min(99.0, downloaded / total * 100))
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            if downloaded < _MIN_FILE_BYTES:
                temp_path.unlink(missing_ok=True)
                status_callback("error", f"Downloaded file too small ({downloaded} bytes)")
                return None

            progress_callback(100.0)
            logger.info(
                "SearXNG download staged %d bytes from %s",
                downloaded,
                urlparse(url).netloc,
            )
            return str(temp_path)

        except requests.RequestException as error:
            if not cancel_flag.is_set():
                logger.warning("SearXNG download failed for %s: %s", url, error)
                status_callback("error", str(error))
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
