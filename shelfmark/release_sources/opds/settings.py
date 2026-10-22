"""Settings registration for the OPDS release source."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    NumberField,
    SettingsField,
    TagListField,
    register_settings,
)
from shelfmark.release_sources.opds.source import DEFAULT_FEEDS


@register_settings("opds_config", "OPDS Catalogs", icon="download", order=48)
def opds_config_settings() -> list[SettingsField]:
    """Register settings for the OPDS release source."""
    return [
        HeadingField(
            key="heading",
            title="OPDS Catalogs",
            description=(
                "Search e-book catalogues that publish an OPDS feed: Project Gutenberg, "
                "Flibusta, or your own Calibre-Web, Kavita, Komga or COPS server. Each "
                "entry is a search URL with {searchTerms}, or the catalogue's root URL. "
                "Put a login in the URL as https://user:password@host/opds; it is only "
                "sent to that host."
            ),
            link_url="https://opds.io/",
            link_text="About OPDS",
        ),
        CheckboxField(
            key="OPDS_ENABLED",
            label="Enable OPDS source",
            description="Include OPDS catalogues in release-source lists and direct search",
            default=False,
            env_supported=True,
        ),
        TagListField(
            key="OPDS_FEEDS",
            label="Catalogs",
            description=(
                "OPDS search URLs ({searchTerms} marks the query) or catalogue root URLs, "
                "searched at the same time"
            ),
            placeholder="https://flibusta.is/opds/opensearch?searchTerm={searchTerms}",
            default=list(DEFAULT_FEEDS),
            normalize_urls=False,
        ),
        NumberField(
            key="OPDS_MAX_RESULTS",
            label="Max Results per Catalog",
            description="Maximum number of books to keep from each catalogue per search",
            default=25,
            min_value=5,
            max_value=100,
        ),
        NumberField(
            key="OPDS_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Seconds to wait for each catalogue request",
            default=20,
            min_value=5,
            max_value=120,
        ),
    ]
