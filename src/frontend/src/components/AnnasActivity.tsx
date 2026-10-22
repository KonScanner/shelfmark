import { useRef, useState, type MouseEvent } from 'react';

import { useMountEffect } from '../hooks/useMountEffect';
import { getAnnasArchiveActivity, isApiResponseError } from '../services/api';
import {
  ACTIVITY_ITEMS_PREVIEW,
  ACTIVITY_KIND_LABEL,
  DISCUSSION_FILES_PREVIEW,
  REPORTS_PAGE_SIZE,
  REPORT_REASON_LABEL,
  activityGroupHeading,
  activityItemKey,
  countReportReasons,
  filterByReason,
  formatActivityTime,
  groupReason,
  pageCount,
  pageItems,
  parseFileFacts,
  primaryReport,
  splitActivityGroups,
  type ActivityGroup,
  type ActivityItem,
  type ActivityItemKind,
  type ActivityMode,
  type ReasonFilter,
  type ReportReason,
} from '../utils/annasActivity';
import { buildTitleSearchHref } from '../utils/recordSearch';

// `signal` aborts when the feed unmounts, which happens as soon as any search starts.
type SearchFile = (group: ActivityGroup, signal: AbortSignal) => Promise<void>;

interface AnnasActivityProps {
  // Searches Shelfmark for the file (by ISBN when known, else by title)
  onSearchFile: SearchFile;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; groups: ActivityGroup[] }
  | { status: 'error'; message: string };

const KIND_CLASS: Record<ActivityItemKind, string> = {
  comment: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  reaction: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  report: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
};

// Reason colours repeat the label's meaning; the label always shows too
const REASON_CLASS: Record<ReportReason, string> = {
  quality: 'text-orange-700 dark:text-orange-300',
  metadata: 'text-violet-700 dark:text-violet-300',
  download: 'text-amber-700 dark:text-amber-300',
  open: 'text-rose-700 dark:text-rose-300',
  spam: 'text-red-700 dark:text-red-300',
  other: 'text-zinc-600 dark:text-zinc-400',
};

const THUMB_SIZE_CLASS = {
  feed: 'h-[4.5rem] w-12',
  row: 'h-10 w-8',
} as const;

const ChevronIcon = ({ direction }: { direction: 'left' | 'right' }) => (
  <svg
    className="h-4 w-4"
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={2}
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d={direction === 'left' ? 'M15.75 19.5 8.25 12l7.5-7.5' : 'm8.25 4.5 7.5 7.5-7.5 7.5'}
    />
  </svg>
);

const RefreshIcon = ({ spinning }: { spinning: boolean }) => (
  <svg
    className={`h-4 w-4 ${spinning ? 'animate-spin' : ''}`}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={2}
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"
    />
  </svg>
);

// The file's cover, or a document icon when Anna's Archive has none (comments and
// reactions never carry one) or the image fails to load.
const FileThumb = ({
  coverUrl,
  size,
}: {
  coverUrl: string | null;
  size: keyof typeof THUMB_SIZE_CLASS;
}) => {
  const [failed, setFailed] = useState(false);
  const sizeClass = THUMB_SIZE_CLASS[size];

  if (coverUrl && !failed) {
    return (
      <img
        src={coverUrl}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className={`${sizeClass} shrink-0 rounded object-cover shadow-sm`}
      />
    );
  }

  return (
    <div
      className={`${sizeClass} flex shrink-0 items-center justify-center rounded border border-(--border-muted) bg-(--bg) text-zinc-400`}
      aria-hidden="true"
    >
      <svg
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
        />
      </svg>
    </div>
  );
};

// The file's title searches Shelfmark for it (by ISBN when Anna's Archive gives one, else
// by title); the arrow opens its Anna's Archive page, which works even when the server
// cannot fetch that page.
const FileHeading = ({
  group,
  onSearchFile,
  className,
}: {
  group: ActivityGroup;
  onSearchFile: SearchFile;
  className: string;
}) => {
  const [searching, setSearching] = useState(false);
  // The lookup can take seconds. If the user starts another search meanwhile, the feed
  // unmounts and this aborts it, so a late answer cannot replace the user's search.
  const searchRef = useRef<AbortController | null>(null);
  useMountEffect(() => () => searchRef.current?.abort());

  const handleSearch = async () => {
    const controller = new AbortController();
    searchRef.current = controller;
    setSearching(true);
    try {
      await onSearchFile(group, controller.signal);
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  };

  // A plain click runs the ISBN lookup in place. Modified and middle clicks keep the
  // browser default, so the title search opens in a new tab or window.
  const handleLinkClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    if (!searching) void handleSearch();
  };

  const titleHref = buildTitleSearchHref(group.title);
  const headingClass = `min-w-0 text-left text-sm leading-snug font-semibold text-sky-700 hover:underline dark:text-sky-400 ${className}`;
  const headingText = searching ? 'Searching…' : activityGroupHeading(group);

  return (
    <div className="flex min-w-0 items-start justify-between gap-1.5">
      {titleHref ? (
        <a
          href={titleHref}
          onClick={handleLinkClick}
          aria-disabled={searching || undefined}
          className={`${headingClass} ${searching ? 'opacity-60' : ''}`}
          title="Search Shelfmark for this file: by ISBN when Anna's Archive gives one, else by title. Open in a new tab to search by title."
        >
          {headingText}
        </a>
      ) : (
        <button
          type="button"
          onClick={() => void handleSearch()}
          disabled={searching}
          className={`${headingClass} disabled:opacity-60`}
          title="Search Shelfmark for this file: by ISBN when Anna's Archive gives one"
        >
          {headingText}
        </button>
      )}
      {group.record_url && (
        <a
          href={group.record_url}
          target="_blank"
          rel="noopener noreferrer"
          className="-mt-0.5 shrink-0 rounded p-1 text-zinc-400 transition-colors hover:text-zinc-700 dark:hover:text-zinc-200"
          aria-label={`Open ${activityGroupHeading(group)} on Anna's Archive`}
          title="Open on Anna's Archive"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
            />
          </svg>
        </a>
      )}
    </div>
  );
};

const ActivityEntry = ({ item }: { item: ActivityItem }) => (
  <li className="flex flex-col gap-1">
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        className={`rounded-full px-1.5 py-px text-[10px] font-semibold tracking-wide uppercase ${KIND_CLASS[item.kind]}`}
      >
        {ACTIVITY_KIND_LABEL[item.kind]}
      </span>
      {item.report_type && (
        <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
          {item.report_type}
        </span>
      )}
    </div>
    {item.text && (
      <p className="line-clamp-4 text-sm break-words whitespace-pre-line text-zinc-700 dark:text-zinc-200">
        {item.text}
      </p>
    )}
    <p className="text-xs text-zinc-500 dark:text-zinc-400">
      {item.user && <span>{item.user}</span>}
      {item.user && item.relative_time && <span aria-hidden="true"> · </span>}
      {item.relative_time && (
        <time dateTime={item.created_at ?? undefined} title={formatActivityTime(item.created_at)}>
          {item.relative_time}
        </time>
      )}
    </p>
  </li>
);

// A file's items, newest first, with the rest behind "Show all"
const ActivityItems = ({ items, className }: { items: ActivityItem[]; className?: string }) => {
  const [expanded, setExpanded] = useState(false);
  const visibleItems = expanded ? items : items.slice(0, ACTIVITY_ITEMS_PREVIEW);

  return (
    <>
      <ul className={`flex flex-col gap-3 ${className ?? ''}`}>
        {visibleItems.map((item) => (
          <ActivityEntry key={activityItemKey(item)} item={item} />
        ))}
      </ul>
      {items.length > ACTIVITY_ITEMS_PREVIEW && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="self-start text-xs font-medium text-sky-600 hover:underline dark:text-sky-400"
          aria-expanded={expanded}
        >
          {expanded ? 'Show less' : `Show all ${items.length}`}
        </button>
      )}
    </>
  );
};

const ReasonTag = ({ reason, reportType }: { reason: ReportReason; reportType: string | null }) => (
  <span
    className={`inline-flex items-center gap-1.5 font-semibold ${REASON_CLASS[reason]}`}
    title={reportType ?? undefined}
  >
    <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
    {REPORT_REASON_LABEL[reason]}
  </span>
);

const FileFactChips = ({ details }: { details: string[] }) => {
  const { format, size, year, language } = parseFileFacts(details);
  const rest = [size, year, language].filter((fact): fact is string => fact !== null);
  if (!format && rest.length === 0) return null;

  const chipClass =
    'rounded border border-(--border-muted) px-1.5 text-[11px] leading-[18px] whitespace-nowrap';
  return (
    <p className="flex flex-wrap gap-1">
      {format && (
        <span className={`${chipClass} font-semibold text-zinc-800 dark:text-zinc-100`}>
          {format}
        </span>
      )}
      {rest.map((fact) => (
        <span key={fact} className={`${chipClass} text-zinc-600 dark:text-zinc-300`}>
          {fact}
        </span>
      ))}
    </p>
  );
};

// The rest of a file's reports, comments and reactions, folded away under the newest report
const MoreNotes = ({ items }: { items: ActivityItem[] }) => {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="self-start text-xs font-medium text-sky-600 hover:underline dark:text-sky-400"
        aria-expanded={open}
      >
        {open
          ? 'Hide other notes'
          : `${items.length} more ${items.length === 1 ? 'note' : 'notes'}`}
      </button>
      {open && <ActivityItems items={items} className="border-l-2 border-(--border-muted) pl-3" />}
    </>
  );
};

const ReportRow = ({ group, onSearchFile }: { group: ActivityGroup; onSearchFile: SearchFile }) => {
  const report = primaryReport(group);
  const otherItems = group.items.filter((item) => item !== report);

  return (
    <li className="flex min-w-0 gap-3.5 border-b border-(--border-muted) py-3.5">
      <FileThumb coverUrl={group.cover_url} size="feed" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <FileHeading group={group} onSearchFile={onSearchFile} className="line-clamp-1" />
        {group.author && (
          <p className="line-clamp-1 text-xs text-zinc-600 dark:text-zinc-300">{group.author}</p>
        )}
        <FileFactChips details={group.details} />
        {report && (
          <>
            <p className="text-xs">
              <ReasonTag reason={groupReason(group)} reportType={report.report_type} />
              {report.relative_time && (
                <span className="text-zinc-500 dark:text-zinc-400">
                  <span aria-hidden="true"> · </span>
                  <time
                    dateTime={report.created_at ?? undefined}
                    title={formatActivityTime(report.created_at)}
                  >
                    {report.relative_time}
                  </time>
                </span>
              )}
            </p>
            {report.text && (
              <p className="line-clamp-3 text-sm break-words whitespace-pre-line text-zinc-700 dark:text-zinc-200">
                {report.text}
              </p>
            )}
          </>
        )}
        {otherItems.length > 0 && <MoreNotes items={otherItems} />}
      </div>
    </li>
  );
};

// Files with only comments and reactions: no title or cover, so a compact list reads
// better than a row of near-empty cards.
const DiscussionList = ({
  groups,
  barTitle,
  onSearchFile,
}: {
  groups: ActivityGroup[];
  barTitle: string;
  onSearchFile: SearchFile;
}) => {
  const [expanded, setExpanded] = useState(false);
  const visibleGroups = expanded ? groups : groups.slice(0, DISCUSSION_FILES_PREVIEW);

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-zinc-600 dark:text-zinc-300">
        Comments &amp; reactions{' '}
        <span className="font-normal text-zinc-500 dark:text-zinc-400">
          ({groups.length.toLocaleString()} {groups.length === 1 ? 'file' : 'files'})
        </span>
      </h3>
      <ul
        className="divide-y divide-(--border-muted) rounded-xl border border-(--border-muted) bg-(--bg-soft)"
        aria-label={`Comments and reactions in ${barTitle}`}
      >
        {visibleGroups.map((group) => (
          <li key={group.md5} className="flex gap-3 px-4 py-3">
            <FileThumb coverUrl={group.cover_url} size="row" />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <FileHeading group={group} onSearchFile={onSearchFile} className="truncate" />
              <ActivityItems items={group.items} />
            </div>
          </li>
        ))}
      </ul>
      {groups.length > DISCUSSION_FILES_PREVIEW && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="self-start text-xs font-medium text-sky-600 hover:underline dark:text-sky-400"
          aria-expanded={expanded}
        >
          {expanded ? 'Show fewer files' : `Show all ${groups.length.toLocaleString()} files`}
        </button>
      )}
    </div>
  );
};

const FeedSkeleton = () => (
  <ul
    className="grid border-t border-(--border-muted) md:grid-cols-2 md:gap-x-8"
    aria-hidden="true"
  >
    {Array.from({ length: REPORTS_PAGE_SIZE }, (_, index) => (
      <li key={index} className="flex gap-3.5 border-b border-(--border-muted) py-3.5">
        <div className="h-[4.5rem] w-12 shrink-0 animate-pulse rounded bg-(--hover-surface)" />
        <div className="flex flex-1 flex-col gap-2 pt-0.5">
          <div className="h-4 w-3/4 animate-pulse rounded bg-(--hover-surface)" />
          <div className="h-3 w-1/3 animate-pulse rounded bg-(--hover-surface)" />
          <div className="h-3 w-1/2 animate-pulse rounded bg-(--hover-surface)" />
          <div className="h-3 w-5/6 animate-pulse rounded bg-(--hover-surface)" />
        </div>
      </li>
    ))}
  </ul>
);

const MODE_LABEL: Record<ActivityMode, string> = { random: 'Random', latest: 'Latest' };

const MODE_DESCRIPTION: Record<ActivityMode, string> = {
  random: "Random files that Anna's Archive readers flagged. Click a title to search for it.",
  latest: "The newest files that Anna's Archive readers flagged. Click a title to search for it.",
};

const REFRESH_LABEL: Record<ActivityMode, string> = {
  random: 'Show other random reports',
  latest: 'Load the newest reports',
};

const MODES: ActivityMode[] = ['random', 'latest'];

// Wait this long after the page is up before loading: the feed is optional and should
// never compete with the search bar for the network.
const START_DELAY_MS = 400;

const INITIAL_LOADS: Record<ActivityMode, LoadState> = {
  random: { status: 'loading' },
  latest: { status: 'loading' },
};

const filterChipClass = (pressed: boolean) =>
  `rounded-full border px-3 py-0.5 text-xs transition-colors ${
    pressed
      ? 'border-zinc-800 bg-zinc-800 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900'
      : 'hover-surface border-(--border-muted) bg-(--bg-soft) text-zinc-600 dark:text-zinc-300'
  }`;

export const AnnasActivity = ({ onSearchFile }: AnnasActivityProps) => {
  const [unavailable, setUnavailable] = useState(false);
  // Both modes load up front, so switching between them is instant
  const [loads, setLoads] = useState(INITIAL_LOADS);
  const [refreshing, setRefreshing] = useState<Record<ActivityMode, boolean>>({
    random: false,
    latest: false,
  });
  const [mode, setMode] = useState<ActivityMode>('random');
  const [reasonFilter, setReasonFilter] = useState<ReasonFilter>('all');
  const [page, setPage] = useState(0);
  const requestsRef = useRef<Partial<Record<ActivityMode, AbortController>>>({});
  const sectionRef = useRef<HTMLElement | null>(null);

  const fetchActivity = async (target: ActivityMode, refresh: boolean) => {
    requestsRef.current[target]?.abort();
    const controller = new AbortController();
    requestsRef.current[target] = controller;
    setRefreshing((previous) => ({ ...previous, [target]: true }));

    try {
      const response = await getAnnasArchiveActivity(target, {
        refresh,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setLoads((previous) => ({
        ...previous,
        [target]: { status: 'ready', groups: response.groups },
      }));
      if (refresh) setPage(0);
    } catch (error) {
      if (controller.signal.aborted) return;
      // 503: Direct Download is off or Anna's Archive is out of reach. The feed is
      // optional, so step aside instead of showing an error on the home page.
      if (isApiResponseError(error) && error.status === 503) {
        setUnavailable(true);
        return;
      }
      setLoads((previous) => ({
        ...previous,
        [target]: {
          status: 'error',
          message: error instanceof Error ? error.message : 'Could not load reports',
        },
      }));
    } finally {
      if (!controller.signal.aborted) {
        setRefreshing((previous) => ({ ...previous, [target]: false }));
      }
    }
  };

  useMountEffect(() => {
    const timer = window.setTimeout(() => {
      for (const target of MODES) void fetchActivity(target, false);
    }, START_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      for (const controller of Object.values(requestsRef.current)) controller.abort();
    };
  });

  if (unavailable) return null;

  const load = loads[mode];
  const groups = load.status === 'ready' ? load.groups : [];
  const { reportGroups, discussionGroups } = splitActivityGroups(groups);
  const reasonCounts = countReportReasons(reportGroups);
  // A refresh can remove the chosen reason; fall back to all reports instead of nothing
  const activeFilter = reasonCounts.some(({ reason }) => reason === reasonFilter)
    ? reasonFilter
    : 'all';
  const filteredGroups = filterByReason(reportGroups, activeFilter);
  const totalPages = pageCount(filteredGroups.length);
  const currentPage = Math.min(page, totalPages - 1);
  const visibleGroups = pageItems(filteredGroups, currentPage);
  const busy = refreshing[mode];
  const headingId = 'annas-activity-heading';

  const selectMode = (next: ActivityMode) => {
    setMode(next);
    setReasonFilter('all');
    setPage(0);
  };

  const selectReason = (next: ReasonFilter) => {
    setReasonFilter(next);
    setPage(0);
  };

  // The pager sits under the feed; bring the top of the new page into view
  const goToPage = (next: number) => {
    setPage(next);
    const section = sectionRef.current;
    if (section && section.getBoundingClientRect().top < 0) {
      section.scrollIntoView({ block: 'start' });
    }
  };

  const iconButtonClass =
    'hover-surface rounded-full p-2 text-zinc-500 transition-colors disabled:cursor-default disabled:opacity-40 dark:text-zinc-400';

  return (
    <section
      ref={sectionRef}
      aria-labelledby={headingId}
      className="mx-auto flex w-full max-w-5xl scroll-mt-4 flex-col gap-4 pb-8"
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id={headingId} className="text-base font-semibold">
            Reader reports
          </h2>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{MODE_DESCRIPTION[mode]}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <div
            className="inline-flex rounded-lg bg-(--hover-surface) p-0.5"
            role="group"
            aria-label="Which reports"
          >
            {MODES.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => selectMode(option)}
                aria-pressed={mode === option}
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  mode === option
                    ? 'bg-(--bg-soft) text-zinc-900 shadow-sm dark:text-zinc-100'
                    : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200'
                }`}
              >
                {MODE_LABEL[option]}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void fetchActivity(mode, true)}
            disabled={busy}
            className={iconButtonClass}
            aria-label={REFRESH_LABEL[mode]}
            title={REFRESH_LABEL[mode]}
          >
            <RefreshIcon spinning={busy} />
          </button>
        </div>
      </div>

      {load.status === 'loading' && <FeedSkeleton />}

      {load.status === 'error' && (
        <div className="flex items-center gap-3 rounded-xl border border-(--border-muted) px-4 py-3 text-sm text-zinc-600 dark:text-zinc-300">
          <span className="min-w-0 flex-1">{load.message}</span>
          <button
            type="button"
            onClick={() => void fetchActivity(mode, true)}
            className="shrink-0 font-medium text-sky-600 hover:underline dark:text-sky-400"
          >
            Try again
          </button>
        </div>
      )}

      {load.status === 'ready' && groups.length === 0 && (
        <p className="rounded-xl border border-(--border-muted) px-4 py-3 text-sm text-zinc-500 dark:text-zinc-400">
          No reports right now.
        </p>
      )}

      <div
        className={`flex flex-col gap-4 transition-opacity ${busy && groups.length > 0 ? 'opacity-60' : ''}`}
      >
        {reportGroups.length > 0 && (
          <>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by reason">
              <button
                type="button"
                onClick={() => selectReason('all')}
                aria-pressed={activeFilter === 'all'}
                className={filterChipClass(activeFilter === 'all')}
              >
                All <span className="opacity-60">{reportGroups.length}</span>
              </button>
              {reasonCounts.map(({ reason, count }) => (
                <button
                  key={reason}
                  type="button"
                  onClick={() => selectReason(reason)}
                  aria-pressed={activeFilter === reason}
                  className={filterChipClass(activeFilter === reason)}
                >
                  {REPORT_REASON_LABEL[reason]} <span className="opacity-60">{count}</span>
                </button>
              ))}
            </div>

            <ul
              className="grid border-t border-(--border-muted) md:grid-cols-2 md:gap-x-8"
              aria-label={`${MODE_LABEL[mode]} reports, page ${currentPage + 1} of ${totalPages}`}
            >
              {visibleGroups.map((group) => (
                <ReportRow key={group.md5} group={group} onSearchFile={onSearchFile} />
              ))}
            </ul>

            {totalPages > 1 && (
              <nav aria-label="Report pages" className="flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={() => goToPage(currentPage - 1)}
                  disabled={currentPage === 0}
                  className={iconButtonClass}
                  aria-label="Previous page"
                >
                  <ChevronIcon direction="left" />
                </button>
                <span className="text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
                  Page {currentPage + 1} of {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => goToPage(currentPage + 1)}
                  disabled={currentPage >= totalPages - 1}
                  className={iconButtonClass}
                  aria-label="Next page"
                >
                  <ChevronIcon direction="right" />
                </button>
              </nav>
            )}
          </>
        )}

        {discussionGroups.length > 0 && (
          <DiscussionList
            groups={discussionGroups}
            barTitle={`${MODE_LABEL[mode]} reports`}
            onSearchFile={onSearchFile}
          />
        )}
      </div>
    </section>
  );
};
