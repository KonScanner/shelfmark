"""OPDS release source.

Searches OPDS catalogues (Project Gutenberg, Flibusta, Calibre-Web and others) and
downloads their files.
"""

# Import to trigger registration
from shelfmark.release_sources.opds import handler as handler
from shelfmark.release_sources.opds import settings as settings
from shelfmark.release_sources.opds import source as source
