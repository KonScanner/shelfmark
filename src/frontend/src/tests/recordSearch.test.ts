import { describe, it, expect } from 'vitest';

import type { Book } from '../types';
import { parseUrlSearchParams } from '../utils/parseUrlSearchParams';
import {
  buildRecordSearchHash,
  buildTitleSearchHref,
  cleanRecordTitle,
  getRecordIsbn,
  pickRecordSearch,
} from '../utils/recordSearch';

const record = (info: Book['info']): Book => ({
  id: '33a195fc',
  title: 'Night (Penguin Twentieth Century Classics)',
  author: 'Elie Wiesel',
  info,
});

describe('recordSearch', () => {
  it('drops trailing bracketed notes from the title', () => {
    expect(cleanRecordTitle('Night (Penguin Twentieth Century Classics)')).toBe('Night');
    expect(cleanRecordTitle('Dune [Ace] (Book 1)')).toBe('Dune');
    expect(cleanRecordTitle('Harry Potter and the Order of the Phoenix: 5')).toBe(
      'Harry Potter and the Order of the Phoenix: 5',
    );
  });

  it('keeps a title that is only a bracketed group', () => {
    expect(cleanRecordTitle('(Untitled)')).toBe('(Untitled)');
  });

  it('prefers ISBN-13 and strips hyphens', () => {
    expect(
      getRecordIsbn(
        record({
          'ISBN-10': ['0-14-018989-0'],
          'ISBN-13': ['978-0-14-018989-6', '978-0-14-103899-5'],
        }),
      ),
    ).toBe('9780140189896');
  });

  it('falls back to ISBN-10, keeping a check digit X', () => {
    expect(getRecordIsbn(record({ 'ISBN-10': ['0-8044-2957-x'] }))).toBe('080442957X');
  });

  it('returns null when the record lists no ISBN', () => {
    expect(getRecordIsbn(record({ Language: ['en'] }))).toBeNull();
    expect(getRecordIsbn(record(undefined))).toBeNull();
  });

  it('builds a hash the URL search parser reads back as the same target and value', () => {
    const parsed = parseUrlSearchParams(
      new URLSearchParams(buildRecordSearchHash('title', 'Night: A Memoir & More')),
    );

    expect(parsed.searchBy).toBe('title');
    expect(parsed.searchInput).toBe('Night: A Memoir & More');
    expect(parsed.contentType).toBe('ebook');
    expect(parsed.resultsTab).toBe('files');
    expect(parsed.hasSearchParams).toBe(true);
  });

  it('links a title to a title search on its clean title', () => {
    const href = buildTitleSearchHref('Dune (Ace Books)');
    const parsed = parseUrlSearchParams(new URLSearchParams(href?.replace(/^#/, '')));

    expect(href?.startsWith('#')).toBe(true);
    expect(parsed.searchBy).toBe('title');
    expect(parsed.searchInput).toBe('Dune');
  });

  it('has no title link for a file without a title', () => {
    expect(buildTitleSearchHref(null)).toBeNull();
    expect(buildTitleSearchHref('   ')).toBeNull();
  });

  it('searches by ISBN when the record lists one', () => {
    expect(pickRecordSearch(record({ 'ISBN-13': ['978-0-14-103899-5'] }), 'Night')).toEqual({
      field: 'isbn',
      value: '9780141038995',
    });
  });

  it("searches by the record's clean title when it lists no ISBN", () => {
    expect(pickRecordSearch(record({ Language: ['en'] }), null)).toEqual({
      field: 'title',
      value: 'Night',
    });
  });

  it('searches by the activity title when the record did not load', () => {
    expect(pickRecordSearch(null, 'Dune (Ace Books)')).toEqual({ field: 'title', value: 'Dune' });
  });

  it('has nothing to search for a file known only by md5 whose record did not load', () => {
    expect(pickRecordSearch(null, null)).toBeNull();
  });
});
