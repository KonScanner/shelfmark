import { describe, it, expect } from 'vitest';

import type { Book } from '../types/index';
import { buildSearchQuery } from '../utils/buildSearchQuery';
import {
  DEFAULT_LANGUAGE_SORT_ORDER,
  NO_FILE_CHIPS,
  displayFileRows,
  formatChipOptions,
  integrateRows,
  isClientSideSort,
  languageChipOptions,
  normalizeFileSort,
  shownPrefixLength,
  sortFileRows,
  universalFilesParams,
} from '../utils/fileResults';
import type { FileSortContext } from '../utils/fileResults';
import { buildLanguageNormalizer } from '../utils/languageFilters';

const book = (id: string, overrides: Partial<Book> = {}): Book => ({
  id,
  title: id,
  author: 'A',
  source: 'direct_download',
  ...overrides,
});

const normalizer = buildLanguageNormalizer([
  { code: 'en', language: 'English' },
  { code: 'el', language: 'Greek' },
  { code: 'de', language: 'German' },
]);

const context: FileSortContext = {
  order: DEFAULT_LANGUAGE_SORT_ORDER,
  normalizer,
  relevanceRank: (b) => Number(b.extra?.rank ?? 0),
};

const ids = (books: Book[]) => books.map((b) => b.id);
const byLanguage = (list: Book[]) => sortFileRows(list, 'language', context);
const priority = (source: string | undefined) =>
  ['direct_download', 'internet_archive', 'libgen'].indexOf(source ?? '');

describe('sortFileRows', () => {
  it('orders by the preferred languages, then everything else', () => {
    const books = ['it', 'none', 'es', 'he', 'el', 'en', 'ru', 'fr', 'de'].map((code) =>
      book(code, code === 'none' ? {} : { language: code }),
    );

    expect(ids(byLanguage(books))).toEqual([
      'en',
      'el',
      'de',
      'ru',
      'he',
      'fr',
      'es',
      'it',
      'none',
    ]);
  });

  it('reads language names and ranks multi-language rows by the first one', () => {
    const books = [book('de', { language: 'German, English' }), book('el', { language: 'Greek' })];

    expect(ids(byLanguage(books))).toEqual(['el', 'de']);
  });

  it('keeps source order inside one language', () => {
    const books = [
      book('a', { language: 'en', downloads: 1 }),
      book('b', { language: 'en', downloads: 900 }),
    ];

    expect(ids(byLanguage(books))).toEqual(['a', 'b']);
  });

  it('sorts by downloads, rows without downloads last', () => {
    const books = [book('none'), book('few', { downloads: 3 }), book('many', { downloads: 30 })];

    expect(ids(sortFileRows(books, 'downloads', context))).toEqual(['many', 'few', 'none']);
  });

  it('sorts by relevance rank', () => {
    const books = [book('b', { extra: { rank: 2 } }), book('a', { extra: { rank: 1 } })];

    expect(ids(sortFileRows(books, 'relevance', context))).toEqual(['a', 'b']);
  });

  it('sorts by year and by size, rows without a value last', () => {
    const books = [
      book('none'),
      book('old', { year: '1965', size: '85.1MB' }),
      book('new', { year: '2020', size: '700KB' }),
    ];

    expect(ids(sortFileRows(books, 'newest', context))).toEqual(['new', 'old', 'none']);
    expect(ids(sortFileRows(books, 'oldest', context))).toEqual(['old', 'new', 'none']);
    expect(ids(sortFileRows(books, 'largest', context))).toEqual(['old', 'new', 'none']);
    expect(ids(sortFileRows(books, 'smallest', context))).toEqual(['new', 'old', 'none']);
  });
});

describe('normalizeFileSort', () => {
  it('maps the stored setting to a file sort', () => {
    expect(normalizeFileSort('')).toBe('downloads');
    expect(normalizeFileSort('newest')).toBe('newest');
    expect(normalizeFileSort('newest_added')).toBe('language');
    expect(normalizeFileSort(undefined)).toBe('language');
  });
});

describe('displayFileRows', () => {
  it('offers a row in another of its formats for a format chip', () => {
    const rows = [
      book('ia:epub', {
        source: 'internet_archive',
        format: 'epub',
        format_variants: { epub: 'ia:epub', pdf: 'ia:pdf' },
      }),
      book('aa', { format: 'epub' }),
      book('nofmt'),
    ];

    const shown = displayFileRows(rows, { languages: [], formats: ['pdf'] }, normalizer);

    expect(shown.map((entry) => [entry.book.id, entry.book.format, entry.index])).toEqual([
      ['ia:pdf', 'pdf', 0],
      ['nofmt', undefined, 2],
    ]);
  });

  it('keeps matching languages first and unknown languages after', () => {
    const rows = [book('unknown'), book('en', { language: 'en' }), book('el', { language: 'el' })];

    const shown = displayFileRows(rows, { languages: ['el'], formats: [] }, normalizer);

    expect(shown.map((entry) => entry.book.id)).toEqual(['el', 'unknown']);
  });
});

describe('chip options', () => {
  it('lists languages in preferred order and formats by count', () => {
    const rows = [
      book('a', { language: 'de', format: 'pdf' }),
      book('b', { language: 'en', format: 'pdf', format_variants: { pdf: 'b', epub: 'b2' } }),
      book('c', { language: 'it', format: 'epub' }),
    ];

    expect(languageChipOptions(rows, context).map((o) => o.value)).toEqual(['en', 'de', 'it']);
    expect(formatChipOptions(rows)).toEqual([
      { value: 'epub', count: 2 },
      { value: 'pdf', count: 2 },
    ]);
  });
});

describe('integrateRows', () => {
  it('sorts the whole list again before the user scrolls', () => {
    const current = [book('it', { language: 'it' })];

    const { rows } = integrateRows(current, [book('en', { language: 'en' })], {
      sort: byLanguage,
      frozenCount: null,
      sourcePriority: priority,
    });

    expect(ids(rows)).toEqual(['en', 'it']);
  });

  it('never moves a frozen row', () => {
    const current = [
      book('shown-it', { language: 'it' }),
      book('shown-de', { language: 'de' }),
      book('unseen-fr', { language: 'fr' }),
    ];
    const incoming = [book('new-en', { language: 'en' }), book('new-es', { language: 'es' })];

    const { rows, added } = integrateRows(current, incoming, {
      sort: byLanguage,
      frozenCount: 2,
      sourcePriority: priority,
    });

    expect(added).toBe(2);
    expect(ids(rows)).toEqual(['shown-it', 'shown-de', 'new-en', 'unseen-fr', 'new-es']);
  });

  it('keeps one row per file, preferring the higher-priority source while unseen', () => {
    const md5 = '0533eb13e300d4959931cab6486d76ed';
    const current = [book(`libgen:${md5}`, { source: 'libgen', md5 })];

    const { rows } = integrateRows(current, [book(md5, { md5 })], {
      sort: (list) => list,
      frozenCount: null,
      sourcePriority: priority,
    });

    expect(rows.map((b) => b.source)).toEqual(['direct_download']);
  });

  it('does not replace a file row the user has seen', () => {
    const md5 = '0533eb13e300d4959931cab6486d76ed';
    const current = [book(`libgen:${md5}`, { source: 'libgen', md5 })];

    const result = integrateRows(current, [book(md5, { md5 })], {
      sort: (list) => list,
      frozenCount: 1,
      sourcePriority: priority,
    });

    expect(result.rows).toBe(current);
    expect(result.added).toBe(0);
  });

  it('skips rows already in the list and repeats inside one batch', () => {
    const md5 = 'f'.repeat(32);
    const current = [book('x', { source: 'libgen' })];
    const incoming = [
      book('x', { source: 'libgen' }),
      book('y', { source: 'libgen', md5 }),
      book('z', { source: 'libgen', md5 }),
    ];

    const { rows, added } = integrateRows(current, incoming, {
      sort: (list) => list,
      frozenCount: null,
      sourcePriority: priority,
    });

    expect(added).toBe(1);
    expect(ids(rows)).toEqual(['x', 'y']);
  });
});

describe('shownPrefixLength', () => {
  it('covers every row up to the last one shown, filtered rows included', () => {
    const rows = [
      book('el1', { language: 'el' }),
      book('en', { language: 'en' }),
      book('el2', { language: 'el' }),
      book('el3', { language: 'el' }),
    ];

    expect(shownPrefixLength(rows, { languages: ['el'], formats: [] }, normalizer, 2)).toBe(3);
    expect(shownPrefixLength(rows, NO_FILE_CHIPS, normalizer, 2)).toBe(2);
  });
});

describe('universalFilesParams', () => {
  it('uses the text query, else an author or title field', () => {
    expect(universalFilesParams('query=%CE%9F%20dune&sort=relevance', {}, 'ebook')).toBe(
      'query=%CE%9F%20dune',
    );
    expect(universalFilesParams('', { author: 'Frank Herbert' }, 'ebook')).toBe(
      'author=Frank%20Herbert',
    );
  });

  it('has no file query for audiobooks or other fields only', () => {
    expect(universalFilesParams('query=dune', {}, 'audiobook')).toBeNull();
    expect(universalFilesParams('', { series: 'Dune' }, 'ebook')).toBeNull();
  });
});

describe('client-side sorts', () => {
  it('are never sent to the server', () => {
    expect(isClientSideSort('language')).toBe(true);
    expect(isClientSideSort('newest')).toBe(true);
    expect(isClientSideSort('')).toBe(true);
    expect(isClientSideSort('newest_added')).toBe(false);

    const query = buildSearchQuery({
      searchInput: 'dune',
      showAdvanced: false,
      advancedFilters: {
        isbn: '',
        author: '',
        title: '',
        lang: ['all'],
        sort: 'language',
        content: '',
        formats: ['epub'],
      },
      bookLanguages: [],
      defaultLanguage: [],
    });

    expect(query).not.toContain('sort=');
  });
});
