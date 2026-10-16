import type { Book, ContentType } from '../types';

/**
 * File results: downloadable rows from several release sources (Anna's Archive, Libgen,
 * Internet Archive, SearXNG, ...), merged, sorted and filtered in the browser.
 */

/** Rows revealed per scroll step. */
export const FILE_BATCH_SIZE = 30;

/** Fallback for the Language Sort Order setting when the server sends none. */
export const DEFAULT_LANGUAGE_SORT_ORDER = ['en', 'el', 'de', 'ru', 'he', 'fr', 'es'];

/** Every file sort runs in the browser, because the rows come from several sources. */
export const FILE_SORT_OPTIONS = [
  { value: 'language', label: 'Preferred languages' },
  { value: 'downloads', label: 'Most downloads' },
  { value: 'relevance', label: 'Most relevant' },
  { value: 'newest', label: 'Newest (publication year)' },
  { value: 'oldest', label: 'Oldest (publication year)' },
  { value: 'largest', label: 'Largest (filesize)' },
  { value: 'smallest', label: 'Smallest (filesize)' },
];

const FILE_SORT_VALUES = new Set(FILE_SORT_OPTIONS.map((option) => option.value));

/** Map a stored sort setting to a file sort: "" is the old "Most downloads" default. */
export const normalizeFileSort = (value: string | undefined): string => {
  if (value === '') return 'downloads';
  return value && FILE_SORT_VALUES.has(value) ? value : 'language';
};

/** Sorts done in the browser; such a sort is never sent to a source. */
export const isClientSideSort = (sort: string | undefined): boolean =>
  !sort || FILE_SORT_VALUES.has(sort);

export interface FileSortContext {
  /** ISO 639-1 codes, first language first. */
  order: string[];
  /** Maps a language code or name (lower case) to its code. */
  normalizer: Map<string, string>;
  /** Source relevance: a lower rank is more relevant. */
  relevanceRank: (book: Book) => number;
}

/** Identity of a file row: the same id can exist in two sources. */
export const fileRowKey = (book: Book): string => `${book.source ?? ''}\u0000${book.id}`;

// Multi-language values ("English, Greek", "en/el") count as their first language.
const LANGUAGE_SEPARATORS = /[,/+&;|]/;

export const primaryLanguageCode = (
  value: string | undefined,
  normalizer: Map<string, string>,
): string | null => {
  const first = value?.split(LANGUAGE_SEPARATORS)[0]?.trim().toLowerCase();
  if (!first) return null;
  return normalizer.get(first) ?? first;
};

const SIZE_UNITS: Record<string, number> = { b: 1, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12 };

const sizeInBytes = (size: string | undefined): number | null => {
  const match = size?.trim().match(/^([\d.,]+)\s*([kmgt]?b)$/i);
  if (!match) return null;
  const amount = Number.parseFloat(match[1].replace(',', '.'));
  const unit = SIZE_UNITS[match[2].toLowerCase()];
  return Number.isFinite(amount) && unit ? amount * unit : null;
};

const yearOf = (year: string | undefined): number | null => {
  const value = Number.parseInt(year ?? '', 10);
  return Number.isFinite(value) ? value : null;
};

/** Sort by a number, largest or smallest first; rows without the number go last. */
const sortByNumber = (
  books: Book[],
  valueOf: (book: Book) => number | null,
  direction: 1 | -1,
): Book[] =>
  books.toSorted((a, b) => {
    const aValue = valueOf(a);
    const bValue = valueOf(b);
    if (aValue === null || bValue === null)
      return (aValue === null ? 1 : 0) - (bValue === null ? 1 : 0);
    return (aValue - bValue) * direction;
  });

const downloadsOf = (book: Book): number | null =>
  book.downloads && book.downloads > 0 ? book.downloads : null;

/**
 * Order file rows for a sort value. Every sort is stable, so rows that tie keep their
 * current order (inside one language, each source keeps its own relevance order).
 */
export const sortFileRows = (books: Book[], sort: string, context: FileSortContext): Book[] => {
  switch (sort) {
    case 'language': {
      const rank = new Map(context.order.map((code, index) => [code, index]));
      const rankOf = (book: Book): number =>
        rank.get(primaryLanguageCode(book.language, context.normalizer) ?? '') ??
        context.order.length;
      return books.toSorted((a, b) => rankOf(a) - rankOf(b));
    }
    case 'downloads':
      return sortByNumber(books, downloadsOf, -1);
    case 'relevance':
      return books.toSorted((a, b) => context.relevanceRank(a) - context.relevanceRank(b));
    case 'newest':
      return sortByNumber(books, (book) => yearOf(book.year), -1);
    case 'oldest':
      return sortByNumber(books, (book) => yearOf(book.year), 1);
    case 'largest':
      return sortByNumber(books, (book) => sizeInBytes(book.size), -1);
    case 'smallest':
      return sortByNumber(books, (book) => sizeInBytes(book.size), 1);
    default:
      return books;
  }
};

export interface FileChips {
  /** Language codes; empty means every language. */
  languages: string[];
  /** Formats; empty means every format. */
  formats: string[];
}

export const NO_FILE_CHIPS: FileChips = { languages: [], formats: [] };

/** A displayed row and the index of the row it comes from. */
export interface DisplayedFileRow {
  book: Book;
  index: number;
}

/** Offer a row in another of its formats (an Internet Archive item, for example). */
const retarget = (book: Book, wanted: Set<string>): Book | null => {
  const variants = book.format_variants;
  if (!variants) return null;
  for (const [format, id] of Object.entries(variants)) {
    if (wanted.has(format)) return { ...book, id, format };
  }
  return null;
};

/**
 * Apply the chips to the rows, in display order.
 *
 * Format chips keep rows in a chosen format, and offer a row with other formats in a
 * chosen one. Language chips keep the matching rows first, then the rows with no known
 * language: those can still be the right file. Rows of another known language go.
 */
export const displayFileRows = (
  rows: Book[],
  chips: FileChips,
  normalizer: Map<string, string>,
): DisplayedFileRow[] => {
  const formats = new Set(chips.formats);
  const languages = new Set(chips.languages);
  const matching: DisplayedFileRow[] = [];
  const unknownLanguage: DisplayedFileRow[] = [];

  rows.forEach((row, index) => {
    let book: Book | null = row;
    const format = row.format?.toLowerCase();
    if (formats.size > 0 && format && !formats.has(format)) {
      book = retarget(row, formats);
    }
    if (!book) return;
    if (languages.size === 0) {
      matching.push({ book, index });
      return;
    }
    const language = primaryLanguageCode(book.language, normalizer);
    if (language === null) unknownLanguage.push({ book, index });
    else if (languages.has(language)) matching.push({ book, index });
  });
  return [...matching, ...unknownLanguage];
};

export interface ChipOption {
  value: string;
  count: number;
}

/** Languages present in the rows, preferred languages first, then by row count. */
export const languageChipOptions = (rows: Book[], context: FileSortContext): ChipOption[] => {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const code = primaryLanguageCode(row.language, context.normalizer);
    if (code) counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  const rank = (code: string) => {
    const index = context.order.indexOf(code);
    return index === -1 ? context.order.length : index;
  };
  return [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .toSorted((a, b) => rank(a.value) - rank(b.value) || b.count - a.count);
};

/** Formats the rows can be offered in (other formats of an item count), most rows first. */
export const formatChipOptions = (rows: Book[]): ChipOption[] => {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const formats = new Set(Object.keys(row.format_variants ?? {}));
    if (row.format) formats.add(row.format.toLowerCase());
    for (const format of formats) counts.set(format, (counts.get(format) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .toSorted((a, b) => b.count - a.count || a.value.localeCompare(b.value));
};

/**
 * Number of leading rows that must not move: everything up to the last row shown.
 * Rows hidden by a chip inside that range stay frozen too.
 */
export const shownPrefixLength = (
  rows: Book[],
  chips: FileChips,
  normalizer: Map<string, string>,
  visibleCount: number,
): number => {
  const shown = displayFileRows(rows, chips, normalizer).slice(0, visibleCount);
  return shown.reduce((end, entry) => Math.max(end, entry.index + 1), 0);
};

export interface IntegrateOptions {
  sort: (books: Book[]) => Book[];
  /** Rows that must keep their place, or null to re-sort the whole list. */
  frozenCount: number | null;
  /** Source priority for the same file from two sources: lower wins. */
  sourcePriority: (source: string | undefined) => number;
}

/**
 * Add rows from a source to the list.
 *
 * A row already in the list is skipped. The same file (by md5) from two sources is
 * kept once: the higher-priority source wins while its row is not shown yet. With no
 * frozen rows, the whole list is sorted again; otherwise only the rows after the
 * frozen ones are, so nothing the user has seen moves.
 */
export const integrateRows = (
  current: Book[],
  incoming: Book[],
  { sort, frozenCount, sourcePriority }: IntegrateOptions,
): { rows: Book[]; added: number } => {
  const keys = new Set(current.map(fileRowKey));
  const md5Owner = new Map<string, number>();
  current.forEach((row, index) => {
    if (row.md5) md5Owner.set(row.md5, index);
  });
  const replaced = new Set<number>();
  const freshMd5 = new Set<string>();
  const fresh: Book[] = [];

  for (const book of incoming) {
    const key = fileRowKey(book);
    if (keys.has(key)) continue;
    keys.add(key);
    if (book.md5) {
      if (freshMd5.has(book.md5)) continue;
      const ownerIndex = md5Owner.get(book.md5);
      if (ownerIndex !== undefined && !replaced.has(ownerIndex)) {
        const owner = current[ownerIndex];
        const ownerMayMove = frozenCount === null || ownerIndex >= frozenCount;
        if (!ownerMayMove || sourcePriority(book.source) >= sourcePriority(owner.source)) continue;
        replaced.add(ownerIndex);
      }
      freshMd5.add(book.md5);
    }
    fresh.push(book);
  }
  if (fresh.length === 0) return { rows: current, added: 0 };

  const kept = current.filter((_, index) => !replaced.has(index));
  if (frozenCount === null) return { rows: sort([...kept, ...fresh]), added: fresh.length };
  const frozen = kept.slice(0, frozenCount);
  return {
    rows: [...frozen, ...sort([...kept.slice(frozenCount), ...fresh])],
    added: fresh.length,
  };
};

/**
 * Build the Files-tab query for a Universal search, or null when there is none.
 *
 * The file sources are ebook sources searched by text: the free-text query when there
 * is one, else an author or title field. Audiobook searches, and searches with only
 * other fields (a series, a list), have no file query.
 */
export const universalFilesParams = (
  query: string,
  fieldValues: Record<string, string | number | boolean> | undefined,
  contentType: ContentType,
): string | null => {
  if (contentType === 'audiobook') return null;
  const text = new URLSearchParams(query).get('query')?.trim();
  if (text) return `query=${encodeURIComponent(text)}`;
  const parts = (['author', 'title'] as const).flatMap((field) => {
    const value = fieldValues?.[field];
    return typeof value === 'string' && value.trim()
      ? [`${field}=${encodeURIComponent(value.trim())}`]
      : [];
  });
  return parts.length > 0 ? parts.join('&') : null;
};
