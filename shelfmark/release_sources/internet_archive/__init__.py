"""Internet Archive release source.

Searches archive.org for freely downloadable texts. Lending-library items, whose
files are encrypted or private, are left out.
"""

# Import to trigger registration
from shelfmark.release_sources.internet_archive import handler as handler
from shelfmark.release_sources.internet_archive import settings as settings
from shelfmark.release_sources.internet_archive import source as source
