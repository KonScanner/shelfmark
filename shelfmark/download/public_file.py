"""Stream a file from a public URL into TMP_DIR.

Shared by the release sources whose releases are plain public file links (SearXNG,
Internet Archive). The URL can come from a client-supplied queue entry, so every hop
is checked against the public-host rule before it is fetched.
"""

import os
import tempfile
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify, prepare_public_url

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from threading import Event

logger = setup_logger(__name__)

_MIN_FILE_BYTES = 1024
_CHUNK_SIZE = 64 * 1024
_MAX_REDIRECTS = 5


# Extensions a staged name may keep: book formats and the archives the download step
# unpacks. Anything else ("1727.epub3.images", "/b/7/fb2") says nothing about the file.
_KNOWN_EXTENSIONS = frozenset(
    {
        "epub", "mobi", "azw", "azw3", "pdf", "fb2", "djvu", "cbz", "cbr", "cb7", "txt",
        "rtf", "doc", "docx", "odt", "lrf", "zip", "rar", "7z", "tar", "gz",
    }
)  # fmt: skip


def _known_extension(name: str) -> str | None:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension if extension in _KNOWN_EXTENSIONS else None


def _file_suffix(response: requests.Response, url: str, fallback_format: str | None = None) -> str:
    """Return the temp-file suffix for a download.

    The download step decides by the suffix whether a file is a book, an archive to
    unpack, or unsupported, so the most reliable name wins: the server's
    Content-Disposition filename, then the final URL after redirects, then the request
    URL, then the release's format.
    """
    disposition = response.headers.get("content-disposition", "")
    if disposition:
        message = Message()
        message["content-disposition"] = disposition
        filename = message.get_filename() or ""
        if extension := _known_extension(filename):
            return f".{extension}"
    final_url = response.url if isinstance(response.url, str) else ""
    for candidate in (final_url, url):
        name = unquote(urlparse(candidate).path).rsplit("/", 1)[-1]
        if extension := _known_extension(name):
            return f".{extension}"
    fallback = (fallback_format or "").strip().lower().lstrip(".")
    return f".{fallback}" if fallback in _KNOWN_EXTENSIONS else ".bin"


def url_origin(url: str) -> str:
    """Return a URL's origin ("https://host:port"), lower-cased, without credentials."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def _check_hop(
    url: str, trusted_origins: Mapping[str, tuple[str, str] | None]
) -> tuple[str | None, tuple[str, str] | None]:
    """Return the URL to fetch for one hop and the login to send with it."""
    origin = url_origin(url)
    if origin in trusted_origins and urlparse(url).scheme in {"http", "https"}:
        return url, trusted_origins[origin]
    return prepare_public_url(url), None


def _open_public_stream(
    url: str, trusted_origins: Mapping[str, tuple[str, str] | None]
) -> requests.Response | None:
    """Open a streamed GET to a public host, re-checking every redirect hop.

    The URL must never reach the container's own network (SearXNG, download clients,
    cloud metadata). The exception is an origin the admin configured as trusted (a
    self-hosted OPDS server on the LAN, for example); only such an origin gets its
    login, and a redirect away from it is checked again. Requests go through the
    configured proxy like every other external fetch.
    """
    current, auth = _check_hop(url, trusted_origins)
    for _ in range(_MAX_REDIRECTS + 1):
        if current is None:
            return None
        response = requests.get(
            current,
            stream=True,
            timeout=(10, 60),
            allow_redirects=False,
            auth=auth,
            proxies=get_proxies(current),
            verify=get_ssl_verify(current),
        )
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        if not location:
            return None
        current, auth = _check_hop(urljoin(current, location), trusted_origins)
    return None


def download_public_file(
    url: str,
    cancel_flag: Event,
    progress_callback: Callable[[float], None],
    status_callback: Callable[[str, str | None], None],
    *,
    source_label: str,
    trusted_origins: Mapping[str, tuple[str, str] | None] | None = None,
    fallback_format: str | None = None,
) -> str | None:
    """Stream ``url`` into TMP_DIR and return the staged path.

    Returns None on cancel or failure; a failure is reported through
    ``status_callback`` first. ``source_label`` names the source in logs and in the
    temp-file name. ``trusted_origins`` maps admin-configured origins to their login
    (or None); those origins may be private hosts. ``fallback_format`` names the file
    type when neither the server nor the URL does.
    """
    try:
        status_callback("resolving", "Connecting to the file host...")
        if cancel_flag.is_set():
            return None

        response = _open_public_stream(url, trusted_origins or {})
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
            suffix = _file_suffix(response, url, fallback_format)
            return _stage_body(
                response, suffix, url, cancel_flag, progress_callback, status_callback, source_label
            )

    except requests.RequestException as error:
        if not cancel_flag.is_set():
            logger.warning("%s download failed for %s: %s", source_label, url, error)
            status_callback("error", str(error))
        return None


def _stage_body(
    response: requests.Response,
    suffix: str,
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
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=TMP_DIR)
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
