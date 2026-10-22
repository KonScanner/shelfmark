import type { Book, ReviewQuery, ReviewSort } from '../types';

// Providers that serve reader reviews.
// Centralised here so the check isn't hardcoded across every view component.
const PROVIDERS_WITH_REVIEWS = new Set(['hardcover']);

/** Returns true if the book's provider can show reader reviews. */
export const bookSupportsReviews = (book: Book): boolean =>
  Boolean(book.provider && book.provider_id && PROVIDERS_WITH_REVIEWS.has(book.provider));

export const REVIEW_SORT_OPTIONS: { value: ReviewSort; label: string }[] = [
  { value: 'popular', label: 'Most liked' },
  { value: 'newest', label: 'Newest' },
  { value: 'oldest', label: 'Oldest' },
  { value: 'highest', label: 'Highest rated' },
  { value: 'lowest', label: 'Lowest rated' },
];

export const REVIEW_STAR_BUCKETS = [5, 4, 3, 2, 1] as const;

export const DEFAULT_REVIEW_QUERY: ReviewQuery = {
  sort: 'popular',
  stars: null,
  hideSpoilers: false,
  page: 1,
};

// Reviews longer than this start collapsed behind "Read more"
export const REVIEW_PREVIEW_CHARS = 600;

export const isReviewSort = (value: string): value is ReviewSort =>
  REVIEW_SORT_OPTIONS.some((option) => option.value === value);

export const buildReviewSearchParams = (query: ReviewQuery): URLSearchParams => {
  const params = new URLSearchParams({ sort: query.sort, page: String(query.page) });
  if (query.stars !== null) {
    params.set('stars', String(query.stars));
  }
  if (query.hideSpoilers) {
    params.set('spoilers', 'hide');
  }
  return params;
};

const reviewDateFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
});

/** Format a review date ("2024-03-13T00:00:00") as a short calendar date. */
export const formatReviewDate = (value: string | null): string | null => {
  const datePart = value?.slice(0, 10);
  if (!datePart || !/^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
    return null;
  }
  // Hardcover stores calendar dates, so read and format them as UTC to keep the day.
  const date = new Date(`${datePart}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : reviewDateFormat.format(date);
};

/** Percent of `total` (0-100) for a histogram bar. */
export const starBucketShare = (count: number, total: number): number =>
  total > 0 ? Math.round((count / total) * 100) : 0;
