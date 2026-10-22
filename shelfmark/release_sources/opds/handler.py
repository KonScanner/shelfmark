"""Download handler for the OPDS release source.

A release id is the file's URL. Configured feed origins are trusted: a self-hosted
catalogue on the LAN can serve its files, and only its own origin gets its login.
"""

from typing import TYPE_CHECKING

from shelfmark.download.public_file import download_public_file
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.opds.source import trusted_origins

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask


@register_handler("opds")
class OPDSHandler(DownloadHandler):
    """Download one file from an OPDS catalogue."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Stream the release's file into TMP_DIR and return the staged path."""
        # The id, not the original download URL: a format chip can switch the row to
        # another file of the same book, and only the id follows that switch.
        url = (task.task_id or "").strip()
        if not url.startswith(("http://", "https://")):
            status_callback("error", "Not an OPDS file URL")
            return None
        return download_public_file(
            url,
            cancel_flag,
            progress_callback,
            status_callback,
            source_label="OPDS",
            trusted_origins=trusted_origins(),
            fallback_format=task.format,
        )

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
