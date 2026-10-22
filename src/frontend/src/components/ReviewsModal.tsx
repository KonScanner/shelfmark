import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useMountEffect } from '../hooks/useMountEffect';
import { getMetadataBookReviews } from '../services/api';
import type { Book, BookReview, BookReviewPage, ReviewQuery } from '../types';
import {
  DEFAULT_REVIEW_QUERY,
  REVIEW_PREVIEW_CHARS,
  REVIEW_SORT_OPTIONS,
  REVIEW_STAR_BUCKETS,
  formatReviewDate,
  isReviewSort,
  starBucketShare,
} from '../utils/bookReviews';
import { StarRating } from './shared';

interface ReviewsModalProps {
  book: Book;
  onClose: () => void;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

type LoadState =
  | { status: 'loading' }
  | { status: 'loading-more' }
  | { status: 'ready' }
  | { status: 'error'; message: string };

const mergeReviews = (current: BookReview[], next: BookReview[]): BookReview[] => {
  // A new review between two page loads shifts the offsets, so drop the repeats.
  const seen = new Set(current.map((review) => review.id));
  return [...current, ...next.filter((review) => !seen.has(review.id))];
};

const ReviewerAvatar = ({ review }: { review: BookReview }) => {
  const [failed, setFailed] = useState(false);
  const name = review.display_name || review.username || '?';

  if (!review.avatar_url || failed) {
    return (
      <div
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-sm font-semibold text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300"
        aria-hidden="true"
      >
        {name.charAt(0).toUpperCase()}
      </div>
    );
  }

  return (
    <img
      src={review.avatar_url}
      alt=""
      className="h-9 w-9 shrink-0 rounded-full object-cover"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
};

const ReviewCard = ({ review }: { review: BookReview }) => {
  const [spoilerRevealed, setSpoilerRevealed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const name = review.display_name || review.username || 'Anonymous reader';
  const date = formatReviewDate(review.reviewed_at);
  const isLong = review.text.length > REVIEW_PREVIEW_CHARS;
  const spoilerHidden = review.has_spoilers && !spoilerRevealed;

  return (
    <li className="border-b border-(--border-muted) px-5 py-4 last:border-b-0">
      <div className="flex items-center gap-3">
        <ReviewerAvatar review={review} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            {review.profile_url ? (
              <a
                href={review.profile_url}
                target="_blank"
                rel="noopener noreferrer"
                className="truncate text-sm font-medium hover:underline"
              >
                {name}
              </a>
            ) : (
              <span className="truncate text-sm font-medium">{name}</span>
            )}
            {review.rating !== null && <StarRating rating={review.rating} size="sm" />}
          </div>
          <div className="flex flex-wrap items-center gap-x-2 text-xs text-zinc-500 dark:text-zinc-400">
            {date && <span>{date}</span>}
            {review.likes > 0 && (
              <span className="flex items-center gap-1">
                <svg
                  className="h-3 w-3"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M21 8.25c0-2.485-2.099-4.5-4.688-4.5-1.935 0-3.597 1.126-4.312 2.733-.715-1.607-2.377-2.733-4.313-2.733C5.1 3.75 3 5.765 3 8.25c0 7.22 9 12 9 12s9-4.78 9-12Z"
                  />
                </svg>
                {review.likes.toLocaleString()} {review.likes === 1 ? 'like' : 'likes'}
              </span>
            )}
            {review.has_spoilers && (
              <span className="rounded-full bg-amber-500/15 px-1.5 py-px text-[10px] font-medium tracking-wide text-amber-700 uppercase dark:text-amber-400">
                Spoilers
              </span>
            )}
          </div>
        </div>
      </div>

      {spoilerHidden ? (
        <div className="relative mt-3 overflow-hidden rounded-lg">
          <p
            className="pointer-events-none line-clamp-3 text-sm leading-relaxed blur-sm select-none"
            aria-hidden="true"
          >
            {review.text}
          </p>
          <button
            type="button"
            onClick={() => setSpoilerRevealed(true)}
            className="absolute inset-0 flex items-center justify-center bg-(--bg)/40 text-sm font-medium text-amber-700 dark:text-amber-400"
          >
            This review has spoilers. Show it.
          </button>
        </div>
      ) : (
        <>
          <p
            className={`mt-3 text-sm leading-relaxed break-words whitespace-pre-line text-zinc-700 dark:text-zinc-200 ${isLong && !expanded ? 'line-clamp-6' : ''}`}
          >
            {review.text}
          </p>
          {isLong && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="mt-1 text-sm font-medium text-sky-600 hover:underline dark:text-sky-400"
              aria-expanded={expanded}
            >
              {expanded ? 'Show less' : 'Read more'}
            </button>
          )}
        </>
      )}
    </li>
  );
};

const ReviewSkeleton = () => (
  <ul aria-hidden="true">
    {[0, 1, 2].map((index) => (
      <li key={index} className="border-b border-(--border-muted) px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 animate-pulse rounded-full bg-zinc-200 dark:bg-zinc-700" />
          <div className="h-3 w-32 animate-pulse rounded bg-zinc-200 dark:bg-zinc-700" />
        </div>
        <div className="mt-3 space-y-2">
          <div className="h-3 w-full animate-pulse rounded bg-zinc-200 dark:bg-zinc-700" />
          <div className="h-3 w-5/6 animate-pulse rounded bg-zinc-200 dark:bg-zinc-700" />
          <div className="h-3 w-2/3 animate-pulse rounded bg-zinc-200 dark:bg-zinc-700" />
        </div>
      </li>
    ))}
  </ul>
);

export const ReviewsModal = ({ book, onClose }: ReviewsModalProps) => {
  const [query, setQuery] = useState<ReviewQuery>(DEFAULT_REVIEW_QUERY);
  const [reviews, setReviews] = useState<BookReview[]>([]);
  const [summary, setSummary] = useState<BookReviewPage | null>(null);
  const [load, setLoad] = useState<LoadState>({ status: 'loading' });
  const requestRef = useRef<AbortController | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const provider = book.provider ?? '';
  const providerId = book.provider_id ?? '';
  const providerName = book.provider_display_name || provider;
  const titleId = `book-reviews-title-${book.id}`;
  const averageRating = Number.parseFloat(
    book.display_fields?.find((field) => field.icon === 'star')?.value ?? '',
  );

  const loadReviews = async (next: ReviewQuery) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setQuery(next);
    setLoad({ status: next.page > 1 ? 'loading-more' : 'loading' });

    try {
      const page = await getMetadataBookReviews(provider, providerId, next, controller.signal);
      if (controller.signal.aborted) return;
      setReviews((current) => (next.page > 1 ? mergeReviews(current, page.reviews) : page.reviews));
      setSummary(page);
      setLoad({ status: 'ready' });
    } catch (error) {
      // A newer request (or closing the modal) cancelled this one: not a failure.
      if (controller.signal.aborted) return;
      setLoad({
        status: 'error',
        message: error instanceof Error ? error.message : 'Could not load reviews',
      });
    }
  };

  const applyFilters = (changes: Partial<ReviewQuery>) => {
    void loadReviews({ ...query, ...changes, page: 1 });
  };

  useMountEffect(() => {
    void loadReviews(DEFAULT_REVIEW_QUERY);
    return () => requestRef.current?.abort();
  });

  // This modal opens over another modal that also closes on Escape. Catch the key in
  // the capture phase so only this one closes, keep Tab inside this dialog (the modal
  // behind it is still focusable), and give focus back to the opener.
  useMountEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();

    const trapTab = (event: KeyboardEvent, dialog: HTMLElement) => {
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      const first = focusable.at(0);
      const last = focusable.at(-1);
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      const focusInside = active instanceof Node && dialog.contains(active);
      if (event.shiftKey && (!focusInside || active === first || active === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (!focusInside || active === last)) {
        event.preventDefault();
        first.focus();
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Tab' && dialogRef.current) {
        trapTab(event, dialogRef.current);
        return;
      }
      if (event.key !== 'Escape') return;
      event.stopImmediatePropagation();
      event.preventDefault();
      onClose();
    };

    window.addEventListener('keydown', handleKeyDown, true);
    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
      opener?.focus();
    };
  });

  const isFiltered = query.stars !== null || query.hideSpoilers;
  const isRefreshing = load.status === 'loading' && summary !== null;
  const reviewTotal = summary?.review_count ?? 0;

  const renderList = () => {
    if (load.status === 'error' && reviews.length === 0) {
      return (
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center text-sm">
          <p className="text-zinc-600 dark:text-zinc-300">{load.message}</p>
          <button
            type="button"
            onClick={() => void loadReviews(query)}
            className="rounded-full bg-sky-600 px-4 py-1.5 font-medium text-white hover:bg-sky-700"
          >
            Try again
          </button>
        </div>
      );
    }

    if (summary === null) {
      return <ReviewSkeleton />;
    }

    if (reviews.length === 0) {
      return (
        <div className="flex flex-col items-center gap-3 px-5 py-12 text-center text-sm text-zinc-600 dark:text-zinc-300">
          <p>
            {isFiltered
              ? 'No reviews match these filters.'
              : `No one has reviewed this book on ${providerName} yet.`}
          </p>
          {isFiltered && (
            <button
              type="button"
              onClick={() => applyFilters({ stars: null, hideSpoilers: false })}
              className="font-medium text-sky-600 hover:underline dark:text-sky-400"
            >
              Clear filters
            </button>
          )}
        </div>
      );
    }

    return (
      <>
        <ul className={isRefreshing ? 'opacity-50 transition-opacity' : 'transition-opacity'}>
          {reviews.map((review) => (
            <ReviewCard key={review.id} review={review} />
          ))}
        </ul>
        <div className="flex flex-col items-center gap-2 px-5 py-5 text-xs text-zinc-500 dark:text-zinc-400">
          {load.status === 'error' && (
            <p className="text-red-600 dark:text-red-400">
              {load.message}{' '}
              <button
                type="button"
                onClick={() => void loadReviews(query)}
                className="font-medium text-sky-600 hover:underline dark:text-sky-400"
              >
                Try again
              </button>
            </p>
          )}
          <span>
            Showing {reviews.length.toLocaleString()} of {summary.total.toLocaleString()}
          </span>
          {summary.has_more && (
            <button
              type="button"
              onClick={() => void loadReviews({ ...query, page: query.page + 1 })}
              disabled={load.status === 'loading-more'}
              className="rounded-full border border-(--border-muted) px-4 py-1.5 text-sm font-medium text-(--text) hover:bg-(--hover-surface) disabled:opacity-60"
            >
              {load.status === 'loading-more' ? 'Loading…' : 'Load more reviews'}
            </button>
          )}
        </div>
      </>
    );
  };

  const modal = (
    <div className="fixed inset-0 z-1100 flex items-stretch justify-center bg-black/50 sm:items-center sm:p-6">
      <button
        type="button"
        className="absolute inset-0 border-0 bg-transparent p-0"
        onClick={onClose}
        tabIndex={-1}
        aria-label="Close reviews"
      />
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="settings-modal-enter relative z-10 flex h-full w-full max-w-2xl flex-col overflow-hidden bg-(--bg) text-(--text) outline-none sm:h-[85vh] sm:rounded-2xl sm:border sm:border-(--border-muted) sm:bg-(--bg-soft) sm:shadow-2xl"
      >
        <header className="flex items-start gap-4 border-b border-(--border-muted) px-5 py-4">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-xs tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
              Reviews on {providerName}
            </p>
            <h3 id={titleId} className="truncate text-lg leading-snug font-semibold">
              {book.title || 'Untitled'}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="hover-action rounded-full p-2 text-zinc-500 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100"
            aria-label="Close reviews"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {summary !== null && (
            <section className="flex flex-col gap-4 border-b border-(--border-muted) px-5 py-4 sm:flex-row sm:items-center sm:gap-6">
              <div className="flex shrink-0 flex-col items-start gap-1 sm:w-32 sm:items-center">
                {Number.isFinite(averageRating) && (
                  <>
                    <span className="text-3xl font-semibold">{averageRating.toFixed(1)}</span>
                    <StarRating rating={averageRating} />
                  </>
                )}
                <span className="text-xs text-zinc-500 dark:text-zinc-400">
                  {reviewTotal.toLocaleString()} {reviewTotal === 1 ? 'review' : 'reviews'}
                </span>
              </div>
              <div
                className="flex min-w-0 flex-1 flex-col gap-1"
                role="group"
                aria-label="Filter by rating"
              >
                {REVIEW_STAR_BUCKETS.map((stars) => {
                  const count = summary.star_counts[String(stars)] ?? 0;
                  const isActive = query.stars === stars;
                  return (
                    <button
                      key={stars}
                      type="button"
                      aria-pressed={isActive}
                      disabled={count === 0 && !isActive}
                      onClick={() => applyFilters({ stars: isActive ? null : stars })}
                      className={`flex items-center gap-2 rounded-md px-2 py-0.5 text-xs transition-colors disabled:cursor-default disabled:opacity-50 ${
                        isActive
                          ? 'bg-amber-500/15 text-(--text)'
                          : 'text-zinc-600 hover:bg-(--hover-surface) dark:text-zinc-300'
                      }`}
                    >
                      <span className="w-12 shrink-0 text-left">
                        {stars} {stars === 1 ? 'star' : 'stars'}
                      </span>
                      <span className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
                        <span
                          className="block h-full rounded-full bg-amber-400"
                          style={{ width: `${starBucketShare(count, reviewTotal)}%` }}
                        />
                      </span>
                      <span className="w-10 shrink-0 text-right tabular-nums">
                        {count.toLocaleString()}
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-(--border-muted) bg-(--bg) px-5 py-2.5 sm:bg-(--bg-soft)">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-zinc-500 dark:text-zinc-400">Sort</span>
              <select
                value={query.sort}
                onChange={(event) => {
                  const { value } = event.target;
                  if (isReviewSort(value)) applyFilters({ sort: value });
                }}
                className="rounded-md border border-(--border-muted) bg-(--bg) px-2 py-1 text-sm text-(--text)"
              >
                {REVIEW_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={query.hideSpoilers}
                onChange={(event) => applyFilters({ hideSpoilers: event.target.checked })}
                className="h-4 w-4 rounded accent-sky-600"
              />
              <span>
                Hide spoilers
                {summary !== null && summary.spoiler_count > 0 && (
                  <span className="text-zinc-500 dark:text-zinc-400">
                    {' '}
                    ({summary.spoiler_count.toLocaleString()})
                  </span>
                )}
              </span>
            </label>
            {query.stars !== null && (
              <button
                type="button"
                onClick={() => applyFilters({ stars: null })}
                className="flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium"
                aria-label={`Remove the ${query.stars} star filter`}
              >
                {query.stars} {query.stars === 1 ? 'star' : 'stars'}
                <span aria-hidden="true">×</span>
              </button>
            )}
            {book.source_url && (
              <a
                href={book.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto text-xs text-sky-600 hover:underline dark:text-sky-400"
              >
                Open on {providerName}
              </a>
            )}
          </div>

          <div aria-live="polite" aria-busy={load.status === 'loading'}>
            {renderList()}
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
};
