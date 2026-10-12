import { describe, it, expect } from 'vitest';

import type { Language } from '../types/index';
import {
  LANGUAGE_OPTION_ALL,
  LANGUAGE_OPTION_DEFAULT,
  buildLanguageNormalizer,
  getCheckedLanguageCodes,
  getLanguageFilterValues,
  getReleaseSearchLanguageParams,
  releaseLanguageMatchesFilter,
  resolveDefaultLanguageCodes,
  toggleLanguageSelection,
} from '../utils/languageFilters';

const supportedLanguages: Language[] = [
  { code: 'en', language: 'English' },
  { code: 'de', language: 'German' },
  { code: 'hu', language: 'Hungarian' },
];

describe('languageFilters release search params', () => {
  it('omits languages when only default selection is active', () => {
    const result = getReleaseSearchLanguageParams([LANGUAGE_OPTION_DEFAULT], supportedLanguages, [
      'en',
    ]);

    expect(result).toBe(undefined);
  });

  it('preserves explicit all-languages selection', () => {
    const result = getReleaseSearchLanguageParams([LANGUAGE_OPTION_ALL], supportedLanguages, [
      'en',
    ]);

    expect(result).toEqual([LANGUAGE_OPTION_ALL]);
  });

  it('resolves explicit language selections to codes', () => {
    const result = getReleaseSearchLanguageParams(['de', 'hu'], supportedLanguages, ['en']);

    expect(result).toEqual(['de', 'hu']);
  });

  it('normalizes legacy default language names when combined with explicit filters', () => {
    const result = getReleaseSearchLanguageParams(
      [LANGUAGE_OPTION_DEFAULT, 'de'],
      supportedLanguages,
      ['english'],
    );

    expect(result).toEqual(['en', 'de']);
  });
});

describe('releaseLanguageMatchesFilter', () => {
  it('matches release language names against legacy default language names', () => {
    const normalizer = buildLanguageNormalizer(supportedLanguages);

    expect(releaseLanguageMatchesFilter('English', ['english'], normalizer)).toBe(true);
  });

  it('keeps English-only issue 948 fallback results with a legacy English default', () => {
    const normalizer = buildLanguageNormalizer(supportedLanguages);
    const issue948Languages = [...Array<string>(48).fill('en'), 'de, en', 'en, es'];

    const visibleLanguages = issue948Languages.filter((language) =>
      releaseLanguageMatchesFilter(language, ['english'], normalizer),
    );

    expect(visibleLanguages).toHaveLength(48);
  });

  it('shows every release when the default language list is explicitly empty', () => {
    // Issue 1384: an empty BOOK_LANGUAGE with the "Default" filter hid all releases.
    const normalizer = buildLanguageNormalizer(supportedLanguages);
    const defaults = resolveDefaultLanguageCodes([], supportedLanguages);
    const selectedCodes =
      getLanguageFilterValues([LANGUAGE_OPTION_DEFAULT], supportedLanguages, defaults) ?? defaults;

    expect(releaseLanguageMatchesFilter('en', selectedCodes, normalizer)).toBe(true);
    expect(releaseLanguageMatchesFilter('German', selectedCodes, normalizer)).toBe(true);
    expect(releaseLanguageMatchesFilter('de, en', selectedCodes, normalizer)).toBe(true);
  });

  it('still filters by a configured default language', () => {
    const normalizer = buildLanguageNormalizer(supportedLanguages);

    expect(releaseLanguageMatchesFilter('en', ['en'], normalizer)).toBe(true);
    expect(releaseLanguageMatchesFilter('de', ['en'], normalizer)).toBe(false);
  });
});

describe('resolveDefaultLanguageCodes', () => {
  it('keeps an explicitly empty default as "no default filter"', () => {
    // The backend stores [] to mean "do not filter"; substituting the first
    // supported language here would make the UI filter where the server does not.
    expect(resolveDefaultLanguageCodes([], supportedLanguages)).toEqual([]);
  });

  it('leaves a configured default untouched', () => {
    expect(resolveDefaultLanguageCodes(['de', 'hu'], supportedLanguages)).toEqual(['de', 'hu']);
  });

  it('falls back to the first supported language only when nothing is configured', () => {
    expect(resolveDefaultLanguageCodes(undefined, supportedLanguages)).toEqual(['en']);
    expect(resolveDefaultLanguageCodes(null, [])).toEqual(['en']);
  });

  it('sends no language filter when an empty default is the whole selection', () => {
    const defaults = resolveDefaultLanguageCodes([], supportedLanguages);

    expect(
      getReleaseSearchLanguageParams([LANGUAGE_OPTION_DEFAULT], supportedLanguages, defaults),
    ).toBe(undefined);
  });

  it('does not smuggle the first language into a Default+German selection', () => {
    const defaults = resolveDefaultLanguageCodes([], supportedLanguages);

    expect(
      getReleaseSearchLanguageParams([LANGUAGE_OPTION_DEFAULT, 'de'], supportedLanguages, defaults),
    ).toEqual(['de']);
  });
});

describe('toggleLanguageSelection', () => {
  const codes = supportedLanguages.map((lang) => lang.code);

  describe('with BOOK_LANGUAGE=all', () => {
    const defaults = [LANGUAGE_OPTION_ALL];

    it('shows the untouched default as every language checked', () => {
      expect(getCheckedLanguageCodes([LANGUAGE_OPTION_DEFAULT], codes, defaults)).toEqual(codes);
    });

    it('removes one language from the full list and keeps the rest', () => {
      const next = toggleLanguageSelection([LANGUAGE_OPTION_DEFAULT], 'en', codes, defaults);
      expect(next).toEqual(['de', 'hu']);
      expect(toggleLanguageSelection(next, 'de', codes, defaults)).toEqual(['hu']);
    });

    it('clears the list when "All languages" is unchecked, then builds a list one by one', () => {
      const cleared = toggleLanguageSelection(
        [LANGUAGE_OPTION_DEFAULT],
        LANGUAGE_OPTION_ALL,
        codes,
        defaults,
      );
      expect(cleared).toEqual([]);
      expect(getCheckedLanguageCodes(cleared, codes, defaults)).toEqual([]);

      const one = toggleLanguageSelection(cleared, 'de', codes, defaults);
      expect(one).toEqual(['de']);
      expect(toggleLanguageSelection(one, 'en', codes, defaults)).toEqual(['en', 'de']);
    });

    it('returns to the default once every language is checked again', () => {
      expect(toggleLanguageSelection(['en', 'de'], 'hu', codes, defaults)).toEqual([
        LANGUAGE_OPTION_DEFAULT,
      ]);
    });

    it('selects every language from a partial list via "All languages"', () => {
      expect(toggleLanguageSelection(['de'], LANGUAGE_OPTION_ALL, codes, defaults)).toEqual([
        LANGUAGE_OPTION_DEFAULT,
      ]);
    });

    it('keeps an empty list when the last language is unchecked', () => {
      expect(toggleLanguageSelection(['de'], 'de', codes, defaults)).toEqual([]);
    });
  });

  describe('with a concrete default language', () => {
    const defaults = ['en'];

    it('shows only the default checked', () => {
      expect(getCheckedLanguageCodes([LANGUAGE_OPTION_DEFAULT], codes, defaults)).toEqual(['en']);
    });

    it('adds a language to the default', () => {
      expect(toggleLanguageSelection([LANGUAGE_OPTION_DEFAULT], 'de', codes, defaults)).toEqual([
        'en',
        'de',
      ]);
    });

    it('narrows explicit all-languages one language at a time', () => {
      expect(toggleLanguageSelection([LANGUAGE_OPTION_ALL], 'hu', codes, defaults)).toEqual([
        'en',
        'de',
      ]);
    });

    it('falls back to the default when the list becomes empty', () => {
      expect(toggleLanguageSelection(['de'], 'de', codes, defaults)).toEqual([
        LANGUAGE_OPTION_DEFAULT,
      ]);
    });

    it('resets to the default when "All languages" is unchecked', () => {
      expect(
        toggleLanguageSelection([LANGUAGE_OPTION_ALL], LANGUAGE_OPTION_ALL, codes, defaults),
      ).toEqual([LANGUAGE_OPTION_DEFAULT]);
    });

    it('selects every language explicitly', () => {
      expect(
        toggleLanguageSelection([LANGUAGE_OPTION_DEFAULT], LANGUAGE_OPTION_ALL, codes, defaults),
      ).toEqual([LANGUAGE_OPTION_ALL]);
    });
  });
});
