"""Download handler for the Z-Library release source.

The account session asks the API for the book's file link, then the file is streamed
like any other public file. A refused session is dropped, so the next attempt logs in
again.
"""

from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.download.public_file import download_public_file
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.zlibrary.source import (
    ZLibraryError,
    account_configured,
    call_api,
    configured_mirrors,
    forget_session,
    session_cookies,
    split_source_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

NO_ACCOUNT_MESSAGE = (
    "Z-Library downloads need an account: set ZLIBRARY_EMAIL and ZLIBRARY_PASSWORD "
    "(or ZLIBRARY_REMIX_USERID and ZLIBRARY_REMIX_USERKEY)"
)


def resolve_download_link(book_id: str, book_hash: str) -> str:
    """Return the file link for a book from the first mirror that gives one.

    Raises:
        ZLibraryError: no account, or no mirror gave a link (the message says why,
            for example that the daily limit is reached).
    """
    if not account_configured():
        raise ZLibraryError(NO_ACCOUNT_MESSAGE)
    errors: list[str] = []
    for base in configured_mirrors():
        try:
            payload = call_api(
                "GET",
                f"/eapi/book/{book_id}/{book_hash}/file",
                cookies=session_cookies(base),
                base=base,
            )
        except ZLibraryError as error:
            forget_session(base)
            errors.append(str(error))
            continue
        raw_file = payload.get("file")
        file_info = raw_file if isinstance(raw_file, dict) else {}
        link = str(file_info.get("downloadLink") or "")
        if link.startswith(("http://", "https://")):
            return link
        errors.append(f"{base}: no download link in the answer")
    raise ZLibraryError("; ".join(errors or ["no Z-Library mirror configured"]))


@register_handler("zlibrary")
class ZLibraryHandler(DownloadHandler):
    """Download a Z-Library book through the configured account."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Ask Z-Library for the file link, then stream the file into TMP_DIR."""
        parts = split_source_id(task.task_id or "")
        if parts is None:
            status_callback("error", "Not a Z-Library release")
            return None

        status_callback("resolving", "Asking Z-Library for the file link...")
        try:
            link = resolve_download_link(*parts)
        except ZLibraryError as error:
            logger.warning("Z-Library download link failed for %s: %s", task.task_id, error)
            status_callback("error", str(error))
            return None
        return download_public_file(
            link,
            cancel_flag,
            progress_callback,
            status_callback,
            source_label="Z-Library",
            fallback_format=task.format,
        )

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
