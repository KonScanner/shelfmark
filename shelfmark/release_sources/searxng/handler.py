"""Download handler for the SearXNG release source.

Streams the file behind a result URL into TMP_DIR and hands the path to the
orchestrator, which extracts archives and moves the payload to its destination.
"""

from typing import TYPE_CHECKING

from shelfmark.download.public_file import download_public_file
from shelfmark.release_sources import DownloadHandler, register_handler

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask


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
        return download_public_file(
            url, cancel_flag, progress_callback, status_callback, source_label="SearXNG"
        )

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
