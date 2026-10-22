"""Download handler for the Project Ben-Yehuda release source."""

from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.download.public_file import download_public_file
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.benyehuda.source import (
    BenYehudaError,
    api_key,
    call_api,
    split_source_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)


def resolve_download_url(text_id: str, fmt: str) -> str:
    """Ask the API for the current file link of a text in one format.

    Raises:
        BenYehudaError: the API refused the request or gave no link.
    """
    params = {"key": api_key(), "view": "basic", "file_format": fmt}
    payload = call_api("GET", f"/texts/{text_id}", params=params)
    url = str(payload.get("download_url") or "")
    if not url.startswith(("http://", "https://")):
        msg = f"no {fmt} download link for text {text_id}"
        raise BenYehudaError(msg)
    return url


@register_handler("benyehuda")
class BenYehudaHandler(DownloadHandler):
    """Download one text from Project Ben-Yehuda."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Ask for the file link, then stream the file into TMP_DIR."""
        parts = split_source_id(task.task_id or "")
        if parts is None:
            status_callback("error", "Not a Project Ben-Yehuda release")
            return None
        if not api_key():
            status_callback("error", "Project Ben-Yehuda needs an API key (BENYEHUDA_API_KEY)")
            return None

        status_callback("resolving", "Asking Project Ben-Yehuda for the file link...")
        try:
            url = resolve_download_url(*parts)
        except BenYehudaError as error:
            logger.warning("Project Ben-Yehuda link failed for %s: %s", task.task_id, error)
            status_callback("error", str(error))
            return None
        return download_public_file(
            url,
            cancel_flag,
            progress_callback,
            status_callback,
            source_label="Ben-Yehuda",
            fallback_format=parts[1],
        )

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled through the cancel flag."""
        return False
