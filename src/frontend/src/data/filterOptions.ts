import { FILE_SORT_OPTIONS } from '../utils/fileResults';

// Direct-mode and Files-tab results are merged from several sources and sorted in the
// browser, so their sort options are the file sorts.
export const SORT_OPTIONS = FILE_SORT_OPTIONS;

// Note: Metadata mode sort options are now dynamic per provider
// They come from the /api/config endpoint as metadata_sort_options

// Direct download mode content type options
export const CONTENT_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'book_nonfiction', label: 'Book (non-fiction)' },
  { value: 'book_fiction', label: 'Book (fiction)' },
  { value: 'book_unknown', label: 'Book (unknown)' },
  { value: 'magazine', label: 'Magazine' },
  { value: 'book_comic', label: 'Comic Book' },
  { value: 'standards_document', label: 'Standards document' },
  { value: 'other', label: 'Other' },
  { value: 'musical_score', label: 'Musical score' },
];
