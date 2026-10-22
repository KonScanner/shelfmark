import { describe, it, expect } from 'vitest';

import type { Book } from '../types';
import {
  DEFAULT_REVIEW_QUERY,
  bookSupportsReviews,
  buildReviewSearchParams,
  formatReviewDate,
  isReviewSort,
  starBucketShare,
} from '../utils/bookReviews';

const book = (overrides: Partial<Book>): Book => ({
  id: '1',
  title: 'Dune',
  author: 'Frank Herbert',
  ...overrides,
});

describe('bookReviews', () => {
  it('supports reviews only for providers that serve them', () => {
    expect(bookSupportsReviews(book({ provider: 'hardcover', provider_id: '312460' }))).toBe(true);
    expect(bookSupportsReviews(book({ provider: 'openlibrary', provider_id: 'OL1W' }))).toBe(false);
    expect(bookSupportsReviews(book({ provider: 'hardcover' }))).toBe(false);
  });

  it('sends only the filters that are set', () => {
    expect(buildReviewSearchParams(DEFAULT_REVIEW_QUERY).toString()).toBe('sort=popular&page=1');
    expect(
      buildReviewSearchParams({ sort: 'lowest', stars: 2, hideSpoilers: true, page: 3 }).toString(),
    ).toBe('sort=lowest&page=3&stars=2&spoilers=hide');
  });

  it('accepts only known sort values', () => {
    expect(isReviewSort('newest')).toBe(true);
    expect(isReviewSort('best')).toBe(false);
  });

  it('formats calendar dates without a time zone shift', () => {
    expect(formatReviewDate('2021-11-13T00:00:00')).toContain('2021');
    expect(formatReviewDate('2021-11-13T00:00:00')).toContain('13');
    expect(formatReviewDate(null)).toBeNull();
    expect(formatReviewDate('not a date')).toBeNull();
  });

  it('computes histogram shares and handles an empty total', () => {
    expect(starBucketShare(216, 519)).toBe(42);
    expect(starBucketShare(3, 0)).toBe(0);
  });
});
