// Anna's Archive community activity (comments, reactions and file reports), grouped by file.

export type ActivityMode = 'latest' | 'random';

export type ActivityItemKind = 'comment' | 'reaction' | 'report';

export interface ActivityItem {
  kind: ActivityItemKind;
  user: string | null;
  text: string | null;
  report_type: string | null;
  created_at: string | null; // ISO 8601, UTC
  relative_time: string | null; // As Anna's Archive shows it, e.g. "2 hours ago"
}

export interface ActivityGroup {
  md5: string;
  title: string | null;
  author: string | null;
  details: string[]; // e.g. ["English [en]", "PDF", "1.2MB"]
  cover_url: string | null; // Only file reports carry a cover
  record_url: string | null; // The file's page on the Anna's Archive mirror in use
  items: ActivityItem[];
}

export interface ActivityResponse {
  mode: ActivityMode;
  groups: ActivityGroup[];
}

// The release source that opens an Anna's Archive file by md5
export const ANNAS_ARCHIVE_RECORD_SOURCE = 'direct_download';

// Items shown on a card before "Show all"
export const ACTIVITY_ITEMS_PREVIEW = 3;

// Files shown in the comments and reactions list before "Show all"
export const DISCUSSION_FILES_PREVIEW = 6;

export const ACTIVITY_KIND_LABEL: Record<ActivityItemKind, string> = {
  comment: 'Comment',
  reaction: 'Reaction',
  report: 'Report',
};

/** Card heading: the file's title, or a short md5 when Anna's Archive gave no title. */
export const activityGroupHeading = (group: ActivityGroup): string =>
  group.title ?? `File ${group.md5.slice(0, 8)}…`;

/** Absolute local time for a tooltip, or undefined when the timestamp is missing. */
export const formatActivityTime = (value: string | null): string | undefined => {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toLocaleString();
};

/**
 * Stable list key: Anna's Archive gives entries no id, so combine what identifies one.
 * The text is part of it because two anonymous entries can share the same second.
 */
export const activityItemKey = (item: ActivityItem): string =>
  [item.kind, item.created_at ?? '', item.user ?? '', item.report_type ?? '', item.text ?? ''].join(
    '|',
  );

/**
 * Split a bar's files: files with a report (they carry a title and often a cover) go on
 * cards; files with only comments and reactions (Anna's Archive gives just their md5) go
 * in the compact list under the cards. Page order is kept in both.
 */
export const splitActivityGroups = (
  groups: ActivityGroup[],
): { reportGroups: ActivityGroup[]; discussionGroups: ActivityGroup[] } => {
  const reportGroups: ActivityGroup[] = [];
  const discussionGroups: ActivityGroup[] = [];
  for (const group of groups) {
    const hasReport = group.items.some((item) => item.kind === 'report');
    (hasReport ? reportGroups : discussionGroups).push(group);
  }
  return { reportGroups, discussionGroups };
};

// Short reasons for Anna's Archive report types. Each type starts with a fixed phrase
// and ends with examples, e.g. "Poor quality (e.g. formatting issues, ...)".
export type ReportReason = 'quality' | 'metadata' | 'download' | 'open' | 'spam' | 'other';

export type ReasonFilter = ReportReason | 'all';

const REPORT_REASON_PREFIXES: ReadonlyArray<readonly [string, ReportReason]> = [
  ['Poor quality', 'quality'],
  ['Incorrect metadata', 'metadata'],
  ['Downloading problems', 'download'],
  // "File can’t be opened" has a curly apostrophe, so stop before it
  ['File can', 'open'],
  ['Spam', 'spam'],
];

const REPORT_REASON_ORDER: ReportReason[] = [
  'quality',
  'metadata',
  'download',
  'open',
  'spam',
  'other',
];

export const REPORT_REASON_LABEL: Record<ReportReason, string> = {
  quality: 'Poor quality',
  metadata: 'Wrong metadata',
  download: 'Download problem',
  open: 'Won’t open',
  spam: 'Spam',
  other: 'Other',
};

// Files shown on one page of the report feed
export const REPORTS_PAGE_SIZE = 10;

export const reportReason = (reportType: string | null): ReportReason =>
  REPORT_REASON_PREFIXES.find(([prefix]) => reportType?.startsWith(prefix))?.[1] ?? 'other';

/** The file's newest report (items come newest first), or undefined when it has none. */
export const primaryReport = (group: ActivityGroup): ActivityItem | undefined =>
  group.items.find((item) => item.kind === 'report');

export const groupReason = (group: ActivityGroup): ReportReason =>
  reportReason(primaryReport(group)?.report_type ?? null);

/** How many files have each reason, most common first; reasons with no files are left out. */
export const countReportReasons = (
  groups: ActivityGroup[],
): Array<{ reason: ReportReason; count: number }> => {
  const counts = new Map<ReportReason, number>();
  for (const group of groups) {
    const reason = groupReason(group);
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return REPORT_REASON_ORDER.filter((reason) => counts.has(reason))
    .map((reason) => ({ reason, count: counts.get(reason) ?? 0 }))
    .toSorted((a, b) => b.count - a.count);
};

export const filterByReason = (groups: ActivityGroup[], filter: ReasonFilter): ActivityGroup[] =>
  filter === 'all' ? groups : groups.filter((group) => groupReason(group) === filter);

/** Number of pages for `total` items; an empty list still has one page. */
export const pageCount = (total: number, size: number = REPORTS_PAGE_SIZE): number =>
  Math.max(1, Math.ceil(total / size));

export const pageItems = <T>(items: T[], page: number, size: number = REPORTS_PAGE_SIZE): T[] =>
  items.slice(page * size, (page + 1) * size);

export interface FileFacts {
  format: string | null;
  size: string | null;
  year: string | null;
  language: string | null;
}

const SIZE_PATTERN = /^\d+(?:\.\d+)?\s?[KMGT]?B$/i;
const YEAR_PATTERN = /^\d{4}$/;
const FORMAT_PATTERN = /^[A-Z][A-Z0-9]{1,4}$/;
const LANGUAGE_CODE_PATTERN = /\[[a-z]{2,3}(?:-[a-z0-9]+)?\]/i;
const LANGUAGE_CODES = /\s*\[[a-z]{2,3}(?:-[a-z0-9]+)?\]/gi;

/**
 * Pick the format, size, year and language out of a file's details. Anna's Archive
 * leaves out the parts it does not know, so classify each part by its shape instead of
 * its position. Content type and source parts (e.g. "📕 Book (fiction)") are dropped.
 */
export const parseFileFacts = (details: string[]): FileFacts => {
  const facts: FileFacts = { format: null, size: null, year: null, language: null };
  for (const part of details) {
    if (!facts.size && SIZE_PATTERN.test(part)) facts.size = part;
    else if (!facts.year && YEAR_PATTERN.test(part)) facts.year = part;
    else if (!facts.format && FORMAT_PATTERN.test(part)) facts.format = part;
    else if (!facts.language && LANGUAGE_CODE_PATTERN.test(part)) {
      // Drop the codes and any leading mark, e.g. "✅ French [fr]" -> "French"
      facts.language = part.replace(LANGUAGE_CODES, '').replace(/^[^\p{L}]+/u, '');
    }
  }
  return facts;
};
