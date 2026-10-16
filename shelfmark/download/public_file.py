"""Stream a file from a public URL into TMP_DIR.

Shared by the release sources whose releases are plain public file links (SearXNG,
Internet Archive). The URL can come from a client-supplied queue entry, so every hop
is checked against the public-host rule before it is fetched.
"""

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify, prepare_public_url

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

logger = setup_logger(__name__)

_MIN_FILE_BYTES = 1024
_CHUNK_SIZE = 64 * 1024
_MAX_REDIRECTS = 5


def _file_suffix(url: str) -> str:
    """Return the file extension of a URL path as a temp-file suffix, or ".bin"."""
    name = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    if "." not in name:
        return ".bin"
    return f".{name.rsplit('.', 1)[-1].lower()}"


def _open_public_stream(url: str) -> requests.Response | None:
    """Open a streamed GET to a public host, re-checking every redirect hop.

    The URL must never reach the container's own network (SearXNG, download clients,
    cloud metadata). Requests go through the configured proxy like every other
    external fetch.
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


def download_public_file(
    url: str,
    cancel_flag: Event,
    progress_callback: Callable[[float], None],
    status_callback: Callable[[str, str | None], None],
    *,
    source_label: str,
) -> str | None:
    """Stream ``url`` into TMP_DIR and return the staged path.

    Returns None on cancel or failure; a failure is reported through
    ``status_callback`` first. ``source_label`` names the source in logs and in the
    temp-file name.
    """
    try:
        status_callback("resolving", "Connecting to the file host...")
        if cancel_flag.is_set():
            return None

        response = _open_public_stream(url)
        if response is None:
            logger.warning("%s download blocked for non-public URL: %s", source_label, url)
            status_callback("error", "Download URL is not a public file host")
            return None

        # The response is streamed: close it on every path so its connection goes back
        # to the pool, whether the body was read in full or not.
        with response:
            response.raise_for_status()
            if cancel_flag.is_set():
                return None
            return _stage_body(
                response, url, cancel_flag, progress_callback, status_callback, source_label
            )

    except requests.RequestException as error:
        if not cancel_flag.is_set():
            logger.warning("%s download failed for %s: %s", source_label, url, error)
            status_callback("error", str(error))
        return None


def _stage_body(
    response: requests.Response,
    url: str,
    cancel_flag: Event,
    progress_callback: Callable[[float], None],
    status_callback: Callable[[str, str | None], None],
    source_label: str,
) -> str | None:
    """Write the response body to a temp file; remove it on cancel, error or a stub."""
    status_callback("downloading", None)
    Path(TMP_DIR).mkdir(parents=True, exist_ok=True)
    prefix = f"{source_label.lower().replace(' ', '_')}_"
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=_file_suffix(url), dir=TMP_DIR)
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
        "%s download staged %d bytes from %s", source_label, downloaded, urlparse(url).netloc
    )
    return str(temp_path)
