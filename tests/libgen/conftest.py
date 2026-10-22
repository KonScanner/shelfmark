"""Shared fixtures for the Libgen tests."""

import pytest

from shelfmark.release_sources.libgen import scraper


@pytest.fixture(autouse=True)
def _clear_mirror_health():
    """Mirror health is module state; one test's failed mirror must not skip in the next."""
    scraper._mirror_failed_at.clear()
    yield
    scraper._mirror_failed_at.clear()
