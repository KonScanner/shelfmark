"""SearXNG release source.

Searches a self-hosted SearXNG instance and turns results that link directly
to book files (by extension) into downloadable releases.
"""

# Import to trigger registration
from shelfmark.release_sources.searxng import handler as handler
from shelfmark.release_sources.searxng import settings as settings
from shelfmark.release_sources.searxng import source as source
