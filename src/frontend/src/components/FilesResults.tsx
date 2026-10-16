import type { KeyboardEvent } from 'react';

import { SearchModeProvider } from '../contexts/SearchModeContext';
import type { FileSourceStatus, UseFilesSearchReturn } from '../hooks/useFilesSearch';
import type { Book, ButtonStateInfo, Language } from '../types';
import type { ChipOption, FileChips } from '../utils/fileResults';
import { ResultsSection } from './ResultsSection';

interface FilesResultsProps {
  files: UseFilesSearchReturn;
  bookLanguages: Language[];
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const toggle = (values: string[], value: string): string[] =>
  values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value];

interface ChipGroupProps {
  label: string;
  options: ChipOption[];
  selected: string[];
  labelFor: (value: string) => string;
  onToggle: (value: string) => void;
}

const ChipGroup = ({ label, options, selected, labelFor, onToggle }: ChipGroupProps) => {
  if (options.length === 0) return null;
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs font-medium tracking-wide uppercase opacity-60">{label}</span>
      {options.map((option) => {
        const isOn = selected.includes(option.value);
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={isOn}
            aria-label={`${labelFor(option.value)}, ${option.count} ${option.count === 1 ? 'file' : 'files'}`}
            onClick={() => onToggle(option.value)}
            title={labelFor(option.value)}
            className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${
              isOn ? 'bg-sky-700 text-white' : 'hover-action border border-(--border-muted)'
            }`}
          >
            {option.value.toUpperCase()} <span className="opacity-70">{option.count}</span>
          </button>
        );
      })}
    </div>
  );
};

const STATUS_DOT: Record<FileSourceStatus['state'], string> = {
  loading: 'animate-pulse bg-sky-500',
  done: 'bg-emerald-500',
  error: 'bg-red-500',
};

const STATUS_TEXT: Record<FileSourceStatus['state'], string> = {
  loading: 'searching...',
  done: '',
  error: 'failed',
};

const SourceStatusLine = ({ statuses }: { statuses: FileSourceStatus[] }) => {
  if (statuses.length === 0) return null;
  return (
    // Not a live region: it changes with every page a source loads, which would be
    // announced over and over. The result count and the list carry the outcome.
    <ul
      aria-label="File sources"
      className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400"
    >
      {statuses.map((status) => (
        <li key={status.name} title={status.error} className="flex items-center gap-1">
          <span
            aria-hidden="true"
            className={`inline-block h-1.5 w-1.5 rounded-full ${STATUS_DOT[status.state]}`}
          />
          <span>
            {status.label}
            {status.state === 'done' ? ` ${status.count}` : ` ${STATUS_TEXT[status.state]}`}
          </span>
        </li>
      ))}
    </ul>
  );
};

const emptyMessage = (files: UseFilesSearchReturn): string => {
  if (files.isLoading || files.isPending) return 'Searching file sources...';
  if (files.allFailed) return 'No file source answered. Search again to retry.';
  return 'No files found.';
};

/**
 * File results from every release source: chips, source status, and the result list
 * with its sort, views and scroll paging. Used by Direct mode and the Files tab.
 */
export const FilesResults = ({
  files,
  bookLanguages,
  onDetails,
  onDownload,
  onGetReleases,
  getButtonState,
  onShowToast,
}: FilesResultsProps) => {
  const languageName = (code: string) =>
    bookLanguages.find((language) => language.code === code)?.language ?? code;
  const setChips = (next: Partial<FileChips>) => files.setChips({ ...files.chips, ...next });
  const total = files.displayed.length;

  return (
    <SearchModeProvider searchMode="direct">
      <div className="mt-4 flex flex-col gap-3">
        <SourceStatusLine statuses={files.statuses} />
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          <ChipGroup
            label="Language"
            options={files.languageOptions}
            selected={files.chips.languages}
            labelFor={languageName}
            onToggle={(value) => setChips({ languages: toggle(files.chips.languages, value) })}
          />
          <ChipGroup
            label="Format"
            options={files.formatOptions}
            selected={files.chips.formats}
            labelFor={(value) => value.toUpperCase()}
            onToggle={(value) => setChips({ formats: toggle(files.chips.formats, value) })}
          />
        </div>
      </div>

      {files.rowCount === 0 ? (
        <div className="mt-6 text-sm opacity-80">{emptyMessage(files)}</div>
      ) : (
        <ResultsSection
          books={files.displayed}
          visible
          onDetails={onDetails}
          onDownload={onDownload}
          onGetReleases={onGetReleases}
          getButtonState={getButtonState}
          getUniversalButtonState={getButtonState}
          sortValue={files.sort}
          onSortChange={files.setSort}
          isLoadingMore={files.isLoadingMore}
          directTotalResults={total > 0 ? (files.hasMore ? `${total}+` : total) : null}
          directVisibleCount={files.visibleCount}
          directHasMore={files.hasMore}
          onRevealMoreDirect={files.revealMore}
          onShowToast={onShowToast}
        />
      )}
    </SearchModeProvider>
  );
};

export type ResultsTab = 'books' | 'files';

const RESULTS_TABS: ResultsTab[] = ['books', 'files'];

/** Element ids that tie the tabs to the panel they control. */
export const resultsTabId = (tab: ResultsTab) => `results-tab-${tab}`;
export const RESULTS_PANEL_ID = 'results-panel';

interface ResultsTabsProps {
  active: ResultsTab;
  booksCount: number;
  filesCount: number;
  filesLoading: boolean;
  onChange: (tab: ResultsTab) => void;
}

/**
 * Books (metadata) and Files (release sources) tabs for Universal mode.
 *
 * Follows the ARIA tabs pattern: one tab stop, arrow keys (and Home/End) move between
 * tabs, and the tabs control the results panel the caller renders.
 */
export const ResultsTabs = ({
  active,
  booksCount,
  filesCount,
  filesLoading,
  onChange,
}: ResultsTabsProps) => {
  const counts: Record<ResultsTab, string> = {
    books: String(booksCount),
    files: filesLoading && filesCount === 0 ? '...' : String(filesCount),
  };
  const labels: Record<ResultsTab, string> = { books: 'Books', files: 'Files' };

  const select = (tab: ResultsTab) => {
    onChange(tab);
    document.getElementById(resultsTabId(tab))?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = RESULTS_TABS.indexOf(active);
    const last = RESULTS_TABS.length - 1;
    const targets: Record<string, number> = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    };
    const target = targets[event.key];
    if (target === undefined) return;
    event.preventDefault();
    select(RESULTS_TABS[target] ?? active);
  };

  return (
    <div
      role="tablist"
      aria-label="Result type"
      className="mt-4 flex gap-2 border-b border-(--border-muted)"
    >
      {RESULTS_TABS.map((tab) => (
        <button
          key={tab}
          id={resultsTabId(tab)}
          type="button"
          role="tab"
          aria-selected={active === tab}
          aria-controls={RESULTS_PANEL_ID}
          tabIndex={active === tab ? 0 : -1}
          onClick={() => onChange(tab)}
          onKeyDown={handleKeyDown}
          className={`flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
            active === tab
              ? 'border-emerald-600 text-gray-900 dark:text-gray-100'
              : 'border-transparent text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100'
          }`}
        >
          {labels[tab]}
          <span className="rounded-full bg-(--hover-action) px-2 py-0.5 text-xs">
            {counts[tab]}
          </span>
        </button>
      ))}
    </div>
  );
};
