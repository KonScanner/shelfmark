// Search Shelfmark for a release-source record (e.g. an Anna's Archive file opened from the
// home-page activity bars) by its title or ISBN.
import type { Book } from '../types';

export type RecordSearchField = 'title' | 'isbn';

export interface RecordSearch {
  field: RecordSearchField;
  value: string;
}

// How long a click on an activity title waits for the file's Anna's Archive page (the
// only place that lists its ISBN) before it searches by title instead.
export const RECORD_LOOKUP_TIMEOUT_MS = 10_000;

// One trailing "(...)" or "[...]" group, e.g. "(Penguin Twentieth Century Classics)"
const TRAILING_BRACKET_GROUP = /\s*[([][^()[\]]*[)\]]\s*$/;

// Anna's Archive lists ISBN-13 first; prefer it, it is what providers index.
const ISBN_INFO_KEYS = ['ISBN-13', 'ISBN-10'] as const;

/**
 * The record's title without trailing series, edition or publisher notes in brackets.
 * Those notes make provider title searches miss ("Night (Penguin ...)" finds King Lear).
 */
export const cleanRecordTitle = (title: string): string => {
  let cleaned = title.trim();
  while (TRAILING_BRACKET_GROUP.test(cleaned)) {
    const next = cleaned.replace(TRAILING_BRACKET_GROUP, '').trim();
    if (!next) break;
    cleaned = next;
  }
  return cleaned;
};

/** The record's first ISBN (13 before 10), digits only, or null when it lists none. */
export const getRecordIsbn = (book: Book): string | null => {
  for (const key of ISBN_INFO_KEYS) {
    const raw = book.info?.[key];
    const first = Array.isArray(raw) ? raw[0] : raw;
    const isbn = first?.replace(/[^0-9Xx]/g, '').toUpperCase();
    if (isbn) return isbn;
  }
  return null;
};

/**
 * Choose how to search for a file: by ISBN when its record lists one, else by its title.
 * `record` is null when the record could not be loaded; `fallbackTitle` is the title the
 * activity page showed (null for files known only by md5). Null means nothing to search.
 */
export const pickRecordSearch = (
  record: Book | null,
  fallbackTitle: string | null,
): RecordSearch | null => {
  const isbn = record ? getRecordIsbn(record) : null;
  if (isbn) return { field: 'isbn', value: isbn };

  const title = cleanRecordTitle(record?.title || fallbackTitle || '');
  return title ? { field: 'title', value: title } : null;
};

/**
 * URL hash that makes the app run a search with this Search By target. It goes through
 * the same path as a pasted shared link, so a provider without the field (e.g. no ISBN
 * field) falls back to a General search for the value.
 */
export const buildRecordSearchHash = (field: RecordSearchField, value: string): string =>
  new URLSearchParams({
    q: value,
    search_by: field,
    content_type: 'ebook',
    results: 'files',
  }).toString();

/**
 * Link for a file title that searches by the cleaned title, or null when there is no
 * title. A plain click looks up the ISBN first; the link serves new tabs and windows.
 */
export const buildTitleSearchHref = (title: string | null): string | null => {
  const cleaned = title ? cleanRecordTitle(title) : '';
  return cleaned ? `#${buildRecordSearchHash('title', cleaned)}` : null;
};

/**
 * Run the main search with a Search By target, through the same path as a pasted shared
 * link: it picks the target (or General when the provider has no such field) and searches.
 */
export const runRecordSearch = ({ field, value }: RecordSearch): void => {
  const hash = buildRecordSearchHash(field, value);
  if (window.location.hash.replace(/^#/, '') === hash) {
    // Assigning the same hash fires no event, so a repeat click would do nothing
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    return;
  }
  window.location.hash = hash;
};
