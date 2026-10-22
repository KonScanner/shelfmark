import { describe, it, expect } from 'vitest';

import {
  activityGroupHeading,
  activityItemKey,
  countReportReasons,
  filterByReason,
  formatActivityTime,
  pageCount,
  pageItems,
  parseFileFacts,
  primaryReport,
  reportReason,
  splitActivityGroups,
  type ActivityGroup,
  type ActivityItem,
} from '../utils/annasActivity';

const group = (overrides: Partial<ActivityGroup> = {}): ActivityGroup => ({
  md5: '094ea6c3cd39c11415508d65740c0aa3',
  title: null,
  author: null,
  details: [],
  cover_url: null,
  record_url: null,
  items: [],
  ...overrides,
});

const item = (overrides: Partial<ActivityItem> = {}): ActivityItem => ({
  kind: 'comment',
  user: '#BauNC5f',
  text: 'scanned',
  report_type: null,
  created_at: '2026-10-18T14:48:24+00:00',
  relative_time: '48 minutes ago',
  ...overrides,
});

const reportedFile = (md5: string, reportType: string): ActivityGroup =>
  group({ md5, items: [item({ kind: 'report', report_type: reportType })] });

describe('annasActivity', () => {
  it('uses the file title as the card heading', () => {
    expect(activityGroupHeading(group({ title: 'Neuroscience' }))).toBe('Neuroscience');
  });

  it('falls back to a short md5 when there is no title', () => {
    expect(activityGroupHeading(group())).toBe('File 094ea6c3…');
  });

  it('gives different entries on one file different keys', () => {
    const comment = item();
    const reaction = item({ kind: 'reaction', text: 'Great file quality' });

    expect(activityItemKey(comment)).not.toBe(activityItemKey(reaction));
  });

  it('gives anonymous entries in the same second different keys', () => {
    const first = item({ kind: 'reaction', user: null, text: 'Great file quality' });
    const second = item({ kind: 'reaction', user: null, text: 'Bad scan' });

    expect(activityItemKey(first)).not.toBe(activityItemKey(second));
  });

  it('formats only valid timestamps', () => {
    expect(formatActivityTime(null)).toBeUndefined();
    expect(formatActivityTime('not a date')).toBeUndefined();
    expect(formatActivityTime('2026-10-18T14:48:24+00:00')).toEqual(expect.any(String));
  });

  it('puts files with a report on cards and the rest in the list, in page order', () => {
    const commentOnly = group({ md5: 'a', items: [item()] });
    const reported = group({ md5: 'b', items: [item(), item({ kind: 'report' })] });
    const reactionOnly = group({ md5: 'c', items: [item({ kind: 'reaction' })] });

    const { reportGroups, discussionGroups } = splitActivityGroups([
      commentOnly,
      reported,
      reactionOnly,
    ]);

    expect(reportGroups.map((g) => g.md5)).toEqual(['b']);
    expect(discussionGroups.map((g) => g.md5)).toEqual(['a', 'c']);
  });

  it('maps report types to short reasons by their fixed opening words', () => {
    expect(reportReason('Poor quality (e.g. formatting issues, poor scan quality)')).toBe(
      'quality',
    );
    expect(reportReason('Incorrect metadata (e.g. title, description, cover image)')).toBe(
      'metadata',
    );
    expect(reportReason('Downloading problems (e.g. can’t connect)')).toBe('download');
    expect(reportReason('File can’t be opened (e.g. corrupted file, DRM)')).toBe('open');
    expect(reportReason('Spam / file should be removed (e.g. advertising)')).toBe('spam');
    expect(reportReason('Other')).toBe('other');
    expect(reportReason('A type Anna’s Archive adds later')).toBe('other');
    expect(reportReason(null)).toBe('other');
  });

  it('uses the first report of a file, not its comments', () => {
    const report = item({ kind: 'report', report_type: 'Other' });
    expect(primaryReport(group({ items: [item(), report] }))).toBe(report);
    expect(primaryReport(group({ items: [item()] }))).toBeUndefined();
  });

  it('counts reasons, most common first, and filters by one', () => {
    const groups = [
      reportedFile('a', 'Spam / file should be removed'),
      reportedFile('b', 'Poor quality (e.g. missing pages)'),
      reportedFile('c', 'Poor quality (e.g. missing pages)'),
    ];

    expect(countReportReasons(groups)).toEqual([
      { reason: 'quality', count: 2 },
      { reason: 'spam', count: 1 },
    ]);
    expect(filterByReason(groups, 'quality').map((g) => g.md5)).toEqual(['b', 'c']);
    expect(filterByReason(groups, 'all')).toBe(groups);
  });

  it('pages a list in memory, with one page for an empty list', () => {
    const items = Array.from({ length: 23 }, (_, index) => index);

    expect(pageCount(23, 10)).toBe(3);
    expect(pageCount(0, 10)).toBe(1);
    expect(pageItems(items, 0, 10)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(pageItems(items, 2, 10)).toEqual([20, 21, 22]);
  });

  it('reads file facts by their shape, whatever their position', () => {
    expect(
      parseFileFacts([
        '✅ French [fr]',
        'PDF',
        '22.7MB',
        '2018',
        '📗 Book (unknown)',
        '🚀/ia/zlib',
      ]),
    ).toEqual({ format: 'PDF', size: '22.7MB', year: '2018', language: 'French' });

    expect(parseFileFacts(['CBZ', '469.4MB', '💬 Comic book', '🚀/lgli'])).toEqual({
      format: 'CBZ',
      size: '469.4MB',
      year: null,
      language: null,
    });

    expect(parseFileFacts(['Russian [ru]', 'FB2', '0.6MB']).language).toBe('Russian');
    expect(parseFileFacts([])).toEqual({ format: null, size: null, year: null, language: null });
  });
});
