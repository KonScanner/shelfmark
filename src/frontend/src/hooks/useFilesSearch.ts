import { useCallback, useMemo, useRef, useState } from 'react';

import { getReleaseSources, searchSourceFiles } from '../services/api';
import type { AppConfig, Book, ReleaseSource } from '../types';
import {
  DEFAULT_LANGUAGE_SORT_ORDER,
  FILE_BATCH_SIZE,
  NO_FILE_CHIPS,
  displayFileRows,
  fileRowKey,
  formatChipOptions,
  integrateRows,
  languageChipOptions,
  normalizeFileSort,
  shownPrefixLength,
  sortFileRows,
} from '../utils/fileResults';
import type { ChipOption, FileChips, FileSortContext } from '../utils/fileResults';
import { buildLanguageNormalizer } from '../utils/languageFilters';
import { describeSearchFailure } from '../utils/searchFailureMessage';

const DEFAULT_PAGES_PER_SEARCH = 2;

export type FileSourceState = 'loading' | 'done' | 'error';

export interface FileSourceStatus {
  name: string;
  label: string;
  state: FileSourceState;
  /** Rows this source added so far. */
  count: number;
  error?: string;
}

interface SourceRun {
  name: string;
  label: string;
  priority: number;
  page: number;
  hasMore: boolean;
  loading: boolean;
  count: number;
  error?: string;
}

/** One search: its query, its sources, and the request controller that cancels it. */
interface FilesRun {
  params: string;
  controller: AbortController;
  started: boolean;
  sources: SourceRun[];
  /** Arrival order of each row, for the "Most relevant" sort. */
  arrival: Map<string, number>;
  /** True once the user scrolls: from then on, rows already shown keep their place. */
  scrolled: boolean;
}

export interface UseFilesSearchReturn {
  /** Query of the current search, or null before the first search. */
  activeParams: string | null;
  /** Rows after the chips, in display order (format chips can change a row's file). */
  displayed: Book[];
  rowCount: number;
  statuses: FileSourceStatus[];
  isLoading: boolean;
  /** Every source failed, so "no files" would be the wrong message. */
  allFailed: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  /** A search waits for `ensureStarted` (the Files tab opened). */
  isPending: boolean;
  visibleCount: number;
  sort: string;
  setSort: (sort: string) => void;
  chips: FileChips;
  setChips: (chips: FileChips) => void;
  languageOptions: ChipOption[];
  formatOptions: ChipOption[];
  search: (params: string, options?: { start?: boolean }) => void;
  ensureStarted: () => void;
  reset: () => void;
  revealMore: () => void;
  findBook: (id: string) => Book | undefined;
}

const isFileSource = (source: ReleaseSource): boolean =>
  source.enabled &&
  Boolean(source.browse_results_are_releases) &&
  (source.supported_content_types ?? ['ebook']).includes('ebook');

const sourceState = (source: SourceRun): FileSourceState => {
  if (source.loading) return 'loading';
  return source.error ? 'error' : 'done';
};

const isAbort = (error: unknown): boolean => error instanceof Error && error.name === 'AbortError';

/**
 * Search every file source separately and merge the rows as each source answers.
 *
 * Each source loads `files_pages_per_search` pages at once, then one more page each
 * time the rows not shown yet run short. Until the user scrolls, the whole list is
 * sorted again as rows arrive; after that, rows already shown keep their place.
 */
export function useFilesSearch(config: AppConfig | null): UseFilesSearchReturn {
  const [activeParams, setActiveParams] = useState<string | null>(null);
  const [rows, setRows] = useState<Book[]>([]);
  const [statuses, setStatuses] = useState<FileSourceStatus[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isPending, setIsPending] = useState(false);
  const [chosenSort, setChosenSort] = useState<string | null>(null);
  const [chips, setChipsState] = useState<FileChips>(NO_FILE_CHIPS);
  const [visibleCount, setVisibleCountState] = useState(FILE_BATCH_SIZE);

  const runRef = useRef<FilesRun | null>(null);
  // Read by callbacks that run after an await; only this hook's setters write them.
  const chipsRef = useRef<FileChips>(NO_FILE_CHIPS);
  const visibleCountRef = useRef(FILE_BATCH_SIZE);
  const sortRef = useRef<string | null>(null);

  const sort = chosenSort ?? normalizeFileSort(config?.default_sort);
  const pagesPerSearch = config?.files_pages_per_search ?? DEFAULT_PAGES_PER_SEARCH;

  const normalizer = useMemo(
    () => buildLanguageNormalizer(config?.book_languages ?? []),
    [config?.book_languages],
  );
  const languageOrder = config?.language_sort_order ?? DEFAULT_LANGUAGE_SORT_ORDER;

  const sortContext = useCallback(
    (run: FilesRun | null): FileSortContext => {
      const priorityOf = new Map(run?.sources.map((source) => [source.name, source.priority]));
      return {
        order: languageOrder,
        normalizer,
        relevanceRank: (book) =>
          (priorityOf.get(book.source ?? '') ?? 99) * 1_000_000 +
          (run?.arrival.get(fileRowKey(book)) ?? 0),
      };
    },
    [languageOrder, normalizer],
  );

  const currentSort = useCallback(
    () => sortRef.current ?? normalizeFileSort(config?.default_sort),
    [config?.default_sort],
  );

  const applyVisibleCount = useCallback((count: number) => {
    visibleCountRef.current = count;
    setVisibleCountState(count);
  }, []);

  const publishStatus = useCallback((run: FilesRun) => {
    if (runRef.current !== run) return;
    setStatuses(
      run.sources.map((source) => ({
        name: source.name,
        label: source.label,
        state: sourceState(source),
        count: source.count,
        error: source.error,
      })),
    );
    setHasMore(run.sources.some((source) => source.hasMore));
    setIsLoadingMore(run.sources.some((source) => source.loading && source.page > 0));
  }, []);

  // Load one page of one source; resolves to whether the source may have more.
  const loadPage = useCallback(
    async (run: FilesRun, source: SourceRun, page: number): Promise<boolean> => {
      if (source.loading || runRef.current !== run) return false;
      source.loading = true;
      publishStatus(run);
      try {
        const result = await searchSourceFiles(
          source.name,
          run.params,
          page,
          run.controller.signal,
        );
        if (runRef.current !== run) return false;
        const fresh = result.books.filter((book) => !run.arrival.has(fileRowKey(book)));
        for (const book of fresh) run.arrival.set(fileRowKey(book), run.arrival.size);
        source.page = page;
        source.count += fresh.length;
        // A page with nothing new means the source has run dry, or repeats itself.
        source.hasMore = result.hasMore && fresh.length > 0;

        const context = sortContext(run);
        const sortValue = currentSort();
        setRows(
          (previous) =>
            integrateRows(previous, fresh, {
              sort: (books) => sortFileRows(books, sortValue, context),
              frozenCount: run.scrolled
                ? shownPrefixLength(previous, chipsRef.current, normalizer, visibleCountRef.current)
                : null,
              sourcePriority: (name) =>
                run.sources.find((entry) => entry.name === name)?.priority ?? 99,
            }).rows,
        );
        return source.hasMore;
      } catch (error) {
        if (runRef.current !== run || isAbort(error)) return false;
        source.hasMore = false;
        source.error = describeSearchFailure(error);
        return false;
      } finally {
        source.loading = false;
        publishStatus(run);
      }
    },
    [currentSort, normalizer, publishStatus, sortContext],
  );

  // Load pages one after another until `lastPage`, or until the source runs dry. Each
  // page waits for the one before it: only that answer says whether another exists.
  const loadPages = useCallback(
    (run: FilesRun, source: SourceRun, lastPage: number): Promise<void> => {
      const loadNext = async (): Promise<void> => {
        if (source.page >= lastPage) return;
        if (await loadPage(run, source, source.page + 1)) await loadNext();
      };
      return loadNext();
    },
    [loadPage],
  );

  const begin = useCallback(
    async (run: FilesRun): Promise<void> => {
      run.started = true;
      setIsPending(false);
      let sources: ReleaseSource[];
      try {
        sources = (await getReleaseSources()).filter(isFileSource);
      } catch (error) {
        if (runRef.current !== run) return;
        setStatuses([
          {
            name: 'sources',
            label: 'Sources',
            state: 'error',
            count: 0,
            error: describeSearchFailure(error),
          },
        ]);
        return;
      }
      if (runRef.current !== run) return;
      run.sources = sources.map((source, priority) => ({
        name: source.name,
        label: source.display_name,
        priority,
        page: 0,
        hasMore: false,
        loading: false,
        count: 0,
      }));
      publishStatus(run);
      await Promise.all(run.sources.map((source) => loadPages(run, source, pagesPerSearch)));
    },
    [loadPages, pagesPerSearch, publishStatus],
  );

  const clearRun = useCallback(() => {
    runRef.current?.controller.abort();
    runRef.current = null;
    setRows([]);
    setStatuses([]);
    setHasMore(false);
    setIsLoadingMore(false);
    setIsPending(false);
    chipsRef.current = NO_FILE_CHIPS;
    setChipsState(NO_FILE_CHIPS);
    applyVisibleCount(FILE_BATCH_SIZE);
  }, [applyVisibleCount]);

  const search = useCallback(
    (params: string, options: { start?: boolean } = {}) => {
      clearRun();
      const run: FilesRun = {
        params,
        controller: new AbortController(),
        started: false,
        sources: [],
        arrival: new Map(),
        scrolled: false,
      };
      runRef.current = run;
      setActiveParams(params);
      if (options.start ?? true) void begin(run);
      else setIsPending(true);
    },
    [begin, clearRun],
  );

  const ensureStarted = useCallback(() => {
    const run = runRef.current;
    if (run && !run.started) void begin(run);
  }, [begin]);

  const reset = useCallback(() => {
    clearRun();
    setActiveParams(null);
  }, [clearRun]);

  const setSort = useCallback(
    (value: string) => {
      sortRef.current = value;
      setChosenSort(value);
      const run = runRef.current;
      if (run) run.scrolled = false;
      const context = sortContext(run);
      setRows((previous) => sortFileRows(previous, value, context));
      applyVisibleCount(FILE_BATCH_SIZE);
    },
    [applyVisibleCount, sortContext],
  );

  const setChips = useCallback(
    (next: FileChips) => {
      chipsRef.current = next;
      setChipsState(next);
      // A new filter is a new view from the top; nothing in it has been seen yet.
      const run = runRef.current;
      if (run) run.scrolled = false;
      applyVisibleCount(FILE_BATCH_SIZE);
    },
    [applyVisibleCount],
  );

  const displayed = useMemo(
    () => displayFileRows(rows, chips, normalizer).map((entry) => entry.book),
    [rows, chips, normalizer],
  );

  const revealMore = useCallback(() => {
    const run = runRef.current;
    if (!run) return;
    const next = Math.min(visibleCountRef.current + FILE_BATCH_SIZE, displayed.length);
    if (next > visibleCountRef.current) {
      // Only a real reveal freezes the rows on screen. The observer also fires when a
      // short list fits the window; late sources must still sort into such a list.
      run.scrolled = true;
      applyVisibleCount(next);
    }
    // Fetch ahead while a batch of unseen rows is still left, so scrolling rarely waits.
    if (displayed.length - next < FILE_BATCH_SIZE) {
      for (const source of run.sources) {
        if (source.hasMore && !source.loading) void loadPages(run, source, source.page + 1);
      }
    }
  }, [applyVisibleCount, displayed.length, loadPages]);

  // One lookup per row render (button state, details), so index the rows by id once.
  // A displayed row wins: a format chip can offer a row under another id.
  const booksById = useMemo(() => {
    const byId = new Map<string, Book>();
    for (const book of rows) byId.set(book.id, book);
    for (const book of displayed) byId.set(book.id, book);
    return byId;
  }, [rows, displayed]);
  const findBook = useCallback((id: string) => booksById.get(id), [booksById]);

  const languageOptions = useMemo(
    () => languageChipOptions(rows, sortContext(null)),
    [rows, sortContext],
  );
  const formatOptions = useMemo(() => formatChipOptions(rows), [rows]);

  return {
    activeParams,
    displayed,
    rowCount: rows.length,
    statuses,
    isLoading: statuses.some((status) => status.state === 'loading'),
    allFailed: statuses.length > 0 && statuses.every((status) => status.state === 'error'),
    isLoadingMore,
    hasMore,
    isPending,
    visibleCount,
    sort,
    setSort,
    chips,
    setChips,
    languageOptions,
    formatOptions,
    search,
    ensureStarted,
    reset,
    revealMore,
    findBook,
  };
}
