"""Settings registration for the Project Ben-Yehuda release source."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SelectField,
    SettingsField,
    register_settings,
)
from shelfmark.release_sources.benyehuda.source import FILE_FORMATS


@register_settings("benyehuda_config", "Project Ben-Yehuda", icon="download", order=50)
def benyehuda_config_settings() -> list[SettingsField]:
    """Register settings for the Project Ben-Yehuda release source."""
    return [
        HeadingField(
            key="heading",
            title="Project Ben-Yehuda",
            description=(
                "Search the free Hebrew literature library at benyehuda.org. The API "
                "needs a key; the site gives one at once, free of charge."
            ),
            link_url="https://benyehuda.org/page/api",
            link_text="Get an API key",
        ),
        CheckboxField(
            key="BENYEHUDA_ENABLED",
            label="Enable Project Ben-Yehuda source",
            description="Include Project Ben-Yehuda in release-source lists and direct search",
            default=False,
            env_supported=True,
        ),
        PasswordField(
            key="BENYEHUDA_API_KEY",
            label="API Key",
            description="Your Project Ben-Yehuda API key",
        ),
        SelectField(
            key="BENYEHUDA_FILE_FORMAT",
            label="File Format",
            description="The format to download texts in",
            options=[{"value": fmt, "label": fmt.upper()} for fmt in FILE_FORMATS],
            default="epub",
        ),
        NumberField(
            key="BENYEHUDA_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Seconds to wait for each API request",
            default=20,
            min_value=5,
            max_value=120,
        ),
    ]
