import type { Language } from '../types';

export const LANGUAGE_OPTION_DEFAULT = 'default';
export const LANGUAGE_OPTION_ALL = 'all';

/**
 * Resolve the language codes the "Default" filter option stands for.
 *
 * An explicitly empty list is a deliberate "no default filter" and is returned as-is;
 * only a missing value falls back to the first supported language. Substituting a
 * language for the empty list would make the UI filter by a language the backend
 * does not apply.
 */
export const resolveDefaultLanguageCodes = (
  configuredDefault: string[] | null | undefined,
  supportedLanguages: Language[],
): string[] => {
  if (Array.isArray(configuredDefault)) {
    return configuredDefault;
  }
  return [supportedLanguages[0]?.code || 'en'];
};

/**
 * True when the configured default applies no language filter: an explicitly empty list,
 * or BOOK_LANGUAGE=all. Treating "all" as a language code made the multi-select jump back
 * to every language on each click.
 */
const defaultCoversAllLanguages = (defaultCodes: string[]): boolean =>
  defaultCodes.length === 0 || defaultCodes.includes(LANGUAGE_OPTION_ALL);

/**
 * Resolve a language selection to the codes a multi-select shows checked, in option order.
 * An empty selection checks nothing; it only occurs when the default covers all languages.
 */
export const getCheckedLanguageCodes = (
  selection: string[],
  optionCodes: string[],
  defaultCodes: string[],
): string[] => {
  if (selection.includes(LANGUAGE_OPTION_ALL)) {
    return [...optionCodes];
  }

  const defaultExpansion = defaultCoversAllLanguages(defaultCodes) ? optionCodes : defaultCodes;
  const checked = new Set(
    selection.flatMap((value) => (value === LANGUAGE_OPTION_DEFAULT ? defaultExpansion : [value])),
  );
  return optionCodes.filter((code) => checked.has(code));
};

/**
 * Apply one click on a language multi-select option and return the new selection.
 *
 * A click on a language toggles only that language, so the user can narrow "All languages"
 * one language at a time or build a list from nothing. A click on "All languages" selects
 * everything, or clears the list when everything is already selected.
 */
export const toggleLanguageSelection = (
  selection: string[],
  clicked: string,
  optionCodes: string[],
  defaultCodes: string[],
): string[] => {
  const defaultIsAll = defaultCoversAllLanguages(defaultCodes);
  // Prefer the untouched default when it already means every language, so the filter
  // badge and the URL stay clean.
  const everyLanguage = defaultIsAll ? [LANGUAGE_OPTION_DEFAULT] : [LANGUAGE_OPTION_ALL];
  // An empty selection means "use the default", so only a default that covers all
  // languages can show an empty list without it snapping back.
  const nothingSelected = defaultIsAll ? [] : [LANGUAGE_OPTION_DEFAULT];

  const checked = new Set(getCheckedLanguageCodes(selection, optionCodes, defaultCodes));
  const allChecked = optionCodes.length > 0 && checked.size === optionCodes.length;

  if (clicked === LANGUAGE_OPTION_ALL) {
    return allChecked ? nothingSelected : everyLanguage;
  }

  if (checked.has(clicked)) {
    checked.delete(clicked);
  } else {
    checked.add(clicked);
  }
  const next = optionCodes.filter((code) => checked.has(code));

  if (next.length === 0) {
    return nothingSelected;
  }
  if (next.length === optionCodes.length) {
    return everyLanguage;
  }
  const defaultSet = new Set(defaultCodes);
  if (!defaultIsAll && next.length === defaultSet.size && next.every((c) => defaultSet.has(c))) {
    return [LANGUAGE_OPTION_DEFAULT];
  }
  return next;
};

export const getLanguageFilterValues = (
  selection: string[],
  supportedLanguages: Language[],
  defaultLanguageCodes: string[] = [],
): string[] | null => {
  if (!selection || selection.length === 0) {
    return null;
  }

  const uniqueSelection = Array.from(new Set(selection.filter(Boolean)));

  if (uniqueSelection.includes(LANGUAGE_OPTION_ALL)) {
    return [LANGUAGE_OPTION_ALL];
  }

  const onlyDefaultSelected =
    uniqueSelection.length === 1 && uniqueSelection[0] === LANGUAGE_OPTION_DEFAULT;
  if (onlyDefaultSelected) {
    return null;
  }

  const languageNormalizer = buildLanguageNormalizer(supportedLanguages);
  const supportedCodes = new Set(supportedLanguages.map((lang) => lang.code.toLowerCase()));
  const defaultCodes = defaultLanguageCodes
    .map((code) => languageNormalizer.get(code.toLowerCase()) ?? code.toLowerCase())
    .filter((code) => supportedCodes.has(code));
  const resolved = new Set<string>();

  uniqueSelection.forEach((code) => {
    if (code === LANGUAGE_OPTION_DEFAULT) {
      defaultCodes.forEach((defaultCode) => resolved.add(defaultCode));
      return;
    }

    const normalizedCode = languageNormalizer.get(code.toLowerCase()) ?? code.toLowerCase();
    if (supportedCodes.has(normalizedCode)) {
      resolved.add(normalizedCode);
    }
  });

  return resolved.size ? Array.from(resolved) : null;
};

/**
 * Resolve language selection for /api/releases requests.
 * - undefined: use backend defaults
 * - ["all"]: disable language filtering
 * - ["en", ...]: explicit filter list
 */
export const getReleaseSearchLanguageParams = (
  selection: string[],
  supportedLanguages: Language[],
  defaultLanguageCodes: string[] = [],
): string[] | undefined => {
  const resolved = getLanguageFilterValues(selection, supportedLanguages, defaultLanguageCodes);
  return resolved === null ? undefined : resolved;
};

/**
 * Build a mapping from language names to codes for normalization.
 * Handles both directions: "english" -> "en" and "en" -> "en"
 */
export const buildLanguageNormalizer = (languages: Language[]): Map<string, string> => {
  const map = new Map<string, string>();
  for (const lang of languages) {
    const code = lang.code.toLowerCase();
    map.set(code, code); // code -> code
    map.set(lang.language.toLowerCase(), code); // name -> code
  }
  return map;
};

/**
 * Check if ALL languages in a multi-language release match the selected filter.
 * Multi-language releases use separators like comma, slash, plus, or ampersand
 * (e.g., "English, Spanish", "English/Spanish", "English + Spanish", "English & Spanish").
 *
 * @param releaseLang - Language string from the release (can be code or full name)
 * @param selectedCodes - Array of selected ISO language codes; null or empty means no filter
 * @param languageNormalizer - Optional map to normalize language names to codes
 */
export const releaseLanguageMatchesFilter = (
  releaseLang: string | undefined,
  selectedCodes: string[] | null,
  languageNormalizer?: Map<string, string>,
): boolean => {
  // An empty list is an explicitly empty BOOK_LANGUAGE ("no default filter"), which the
  // backend does not filter on either; treating it as "match nothing" hid every release.
  if (!releaseLang || !selectedCodes || selectedCodes.length === 0) {
    return true;
  }
  if (selectedCodes.includes(LANGUAGE_OPTION_ALL)) {
    return true;
  }

  // Split by common multi-language separators: comma, slash, plus, ampersand
  const releaseParts = releaseLang
    .split(/[,/+&]/)
    .map((l) => l.trim().toLowerCase())
    .filter(Boolean);

  // Normalize release language parts to codes (handles both "en" and "english")
  const releaseCodes = releaseParts.map((part) => {
    if (languageNormalizer) {
      return languageNormalizer.get(part) ?? part;
    }
    return part;
  });

  const selectedSet = new Set(
    selectedCodes.map((code) => {
      const normalizedCode = code.toLowerCase();
      return languageNormalizer?.get(normalizedCode) ?? normalizedCode;
    }),
  );
  return releaseCodes.every((code) => selectedSet.has(code));
};
