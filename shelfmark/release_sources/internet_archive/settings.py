"""Settings registration for the Internet Archive release source."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    NumberField,
    SettingsField,
    register_settings,
)


@register_settings("internet_archive_config", "Internet Archive", icon="download", order=47)
def internet_archive_config_settings() -> list[SettingsField]:
    """Register settings for the Internet Archive release source."""
    return [
        HeadingField(
            key="heading",
            title="Internet Archive",
            description=(
                "Search archive.org for texts that anyone can download. Lending-library "
                "books, which archive.org only lends, are left out."
            ),
            link_url="https://archive.org/advancedsearch.php",
            link_text="archive.org advanced search",
        ),
        CheckboxField(
            key="INTERNET_ARCHIVE_ENABLED",
            label="Enable Internet Archive source",
            description="Include Internet Archive in release-source lists and direct search",
            default=False,
            env_supported=True,
        ),
        NumberField(
            key="INTERNET_ARCHIVE_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of archive.org items to keep per search",
            default=50,
            min_value=5,
            max_value=200,
        ),
        NumberField(
            key="INTERNET_ARCHIVE_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Seconds to wait for the archive.org search API to answer",
            default=20,
            min_value=5,
            max_value=120,
        ),
    ]
