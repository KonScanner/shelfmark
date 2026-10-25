import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ActivityCard } from '../components/activity/ActivityCard';
import type { ActivityItem } from '../components/activity/activityTypes';

describe('ActivityCard markup', () => {
  it('does not nest tooltip wrappers in a title paragraph', () => {
    const item: ActivityItem = {
      id: 'book-1',
      kind: 'download',
      visualStatus: 'complete',
      title: 'Completed book',
      author: 'Author',
      metaLine: 'EPUB',
      statusLabel: 'Complete',
      timestamp: 1,
      downloadBookId: 'book-1',
      downloadPath: '/books/completed.epub',
    };

    const markup = renderToStaticMarkup(createElement(ActivityCard, { item, isAdmin: false }));

    expect(markup).toContain('href="/api/localdownload?id=book-1"');
    expect(markup).not.toMatch(/<p[^>]*><span class="font-semibold"><div/);
  });
});
