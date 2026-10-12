"""Settings registration for the SearXNG release source."""

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    SettingsField,
    TextField,
    register_settings,
)

logger = setup_logger(__name__)


def _test_searxng_connection() -> dict[str, object]:
    """Run one JSON search against the configured SearXNG instance."""
    base_url = str(config.get("SEARXNG_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return {"success": False, "message": "Base URL is not configured"}

    timeout = _coerce_int(config.get("SEARXNG_TIMEOUT", 20), 20)
    try:
        response = requests.get(
            f"{base_url}/search",
            params={"q": "book", "format": "json"},
            timeout=timeout,
        )
    except requests.RequestException as error:
        return {"success": False, "message": f"Connection failed: {error}"}

    if response.status_code == 403:
        return {
            "success": False,
            "message": "SearXNG refused the request. Enable JSON format on the instance.",
        }
    if response.status_code != 200:
        return {"success": False, "message": f"HTTP {response.status_code}"}

    try:
        results = response.json().get("results", [])
    except ValueError:
        return {"success": False, "message": "Response was not JSON"}

    return {
        "success": True,
        "message": f"Connected. {len(results)} result(s) for the test query.",
    }


def _coerce_int(value: object, default: int) -> int:
    """Return an int from loose config storage, or the default."""
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return default
    return parsed


@register_settings("searxng_config", "SearXNG", icon="search", order=46)
def searxng_config_settings() -> list[SettingsField]:
    """Register settings for the SearXNG release source."""
    return [
        HeadingField(
            key="heading",
            title="SearXNG",
            description=(
                "Search a self-hosted SearXNG instance. Only results that link "
                "directly to a book file (by extension) become releases."
            ),
            link_url="https://docs.searxng.org/admin/search-api.html",
            link_text="SearXNG search API documentation",
        ),
        CheckboxField(
            key="SEARXNG_ENABLED",
            label="Enable SearXNG source",
            description="Include SearXNG in release-source lists",
            default=False,
            env_supported=True,
        ),
        TextField(
            key="SEARXNG_BASE_URL",
            label="Base URL",
            placeholder="http://host.docker.internal:47821",
            description=(
                "SearXNG instance URL as seen from the Shelfmark container. "
                "A server on the Docker host is host.docker.internal, not localhost. "
                "JSON output must be enabled on the instance."
            ),
            env_supported=True,
        ),
        TextField(
            key="SEARXNG_CATEGORIES",
            label="Categories",
            description="Comma-separated SearXNG categories to search, e.g. general, files",
            default="general",
            env_supported=True,
        ),
        TextField(
            key="SEARXNG_ENGINES",
            label="Engines",
            description="Comma-separated engine names. Leave empty to use all enabled engines.",
            default="",
            env_supported=True,
        ),
        TextField(
            key="SEARXNG_LANGUAGE",
            label="Language",
            description="SearXNG language code, e.g. el or en. Leave empty to search all languages.",
            default="",
            env_supported=True,
        ),
        NumberField(
            key="SEARXNG_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of file-link results to keep per search",
            default=30,
            min_value=5,
            max_value=100,
        ),
        NumberField(
            key="SEARXNG_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Seconds to wait for the SearXNG API to answer",
            default=20,
            min_value=5,
            max_value=120,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            callback=_test_searxng_connection,
            style="primary",
        ),
    ]
