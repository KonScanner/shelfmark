"""Z-Library release source.

Searches Z-Library through its app API; downloads use a configured account.
"""

# Import to trigger registration
from shelfmark.release_sources.zlibrary import handler as handler
from shelfmark.release_sources.zlibrary import settings as settings
from shelfmark.release_sources.zlibrary import source as source
