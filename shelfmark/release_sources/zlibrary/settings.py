"""Settings registration for the Z-Library release source."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)


@register_settings("zlibrary_config", "Z-Library Search", icon="download", order=49)
def zlibrary_config_settings() -> list[SettingsField]:
    """Register settings for the Z-Library release source."""
    return [
        HeadingField(
            key="heading",
            title="Z-Library Search",
            description=(
                "Search Z-Library's catalogue through its app API. Search works without "
                "an account; downloads need one, and each account has a daily download "
                "limit. The mirrors are the Z-Library mirrors under Direct Download → "
                "Mirrors (ZLIB_MIRROR_URLS)."
            ),
        ),
        CheckboxField(
            key="ZLIBRARY_SEARCH_ENABLED",
            label="Enable Z-Library source",
            description="Include Z-Library in release-source lists and direct search",
            default=False,
            env_supported=True,
        ),
        TextField(
            key="ZLIBRARY_EMAIL",
            label="Account Email",
            description="Z-Library account email, used only to download",
        ),
        PasswordField(
            key="ZLIBRARY_PASSWORD",
            label="Account Password",
            description="Z-Library account password",
        ),
        TextField(
            key="ZLIBRARY_REMIX_USERID",
            label="remix_userid",
            description=(
                "Instead of email and password: the remix_userid cookie of a logged-in "
                "Z-Library session"
            ),
        ),
        PasswordField(
            key="ZLIBRARY_REMIX_USERKEY",
            label="remix_userkey",
            description="The remix_userkey cookie that goes with remix_userid",
        ),
        NumberField(
            key="ZLIBRARY_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of books to ask for per search page",
            default=50,
            min_value=10,
            max_value=100,
        ),
        NumberField(
            key="ZLIBRARY_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Seconds to wait for each Z-Library API request",
            default=20,
            min_value=5,
            max_value=120,
        ),
    ]
