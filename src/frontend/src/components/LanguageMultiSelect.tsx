import type { Language } from '../types';
import {
  LANGUAGE_OPTION_ALL,
  getCheckedLanguageCodes,
  toggleLanguageSelection,
} from '../utils/languageFilters';
import type { DropdownListOption } from './DropdownList';
import { DropdownList } from './DropdownList';

interface LanguageMultiSelectProps {
  options: Language[];
  value: string[];
  onChange: (value: string[]) => void;
  defaultLanguageCodes: string[];
  label?: string;
  placeholder?: string;
}

export const LanguageMultiSelect = ({
  options,
  value,
  onChange,
  defaultLanguageCodes,
  label,
  placeholder,
}: LanguageMultiSelectProps) => {
  const defaultCodeSet = new Set(defaultLanguageCodes);

  // Get default languages with their full info
  const defaultLanguages = options.filter((lang) => defaultCodeSet.has(lang.code));
  const nonDefaultLanguages = options.filter((lang) => !defaultCodeSet.has(lang.code));

  const optionCodes = options.map((lang) => lang.code);

  // Build option list: All, then defaults (marked), then others
  const optionList: DropdownListOption[] = [
    {
      value: LANGUAGE_OPTION_ALL,
      label: 'All languages',
    },
    // Each default language as a separate option
    ...defaultLanguages.map((lang) => ({
      value: lang.code,
      label: `${lang.language} (default)`,
    })),
    // Non-default languages
    ...nonDefaultLanguages.map((lang) => ({
      value: lang.code,
      label: lang.language,
    })),
  ];

  const checkedCodes = getCheckedLanguageCodes(value, optionCodes, defaultLanguageCodes);
  const selectedSet = new Set(checkedCodes);
  const isAllSelected = optionCodes.length > 0 && checkedCodes.length === optionCodes.length;
  const displayedValue = isAllSelected ? [LANGUAGE_OPTION_ALL, ...checkedCodes] : checkedCodes;

  const summaryFormatter = (_selected: DropdownListOption[], fallback: string) => {
    if (isAllSelected) {
      return 'All languages';
    }

    const labels: string[] = [];

    // Check each language
    options.forEach((lang) => {
      if (selectedSet.has(lang.code)) {
        labels.push(lang.language);
      }
    });

    if (labels.length === 0) {
      return placeholder || fallback || 'Choose languages';
    }

    if (labels.length === 1) {
      return labels[0];
    }

    const [first, second, ...rest] = labels;
    const suffix = rest.length > 0 ? ` +${rest.length}` : '';
    return `${first}, ${second ?? ''}${suffix}`.trim();
  };

  // DropdownList reports the whole next list; the one value that differs is the click.
  const handleChange = (nextValue: string[] | string) => {
    const nextArray = Array.isArray(nextValue) ? nextValue : [nextValue];
    const shown = new Set(displayedValue);
    const next = new Set(nextArray);
    const clicked =
      nextArray.find((code) => !shown.has(code)) ?? displayedValue.find((code) => !next.has(code));
    if (clicked === undefined) {
      return;
    }
    onChange(toggleLanguageSelection(value, clicked, optionCodes, defaultLanguageCodes));
  };

  return (
    <DropdownList
      label={label}
      options={optionList}
      multiple
      showCheckboxes
      value={displayedValue}
      onChange={handleChange}
      placeholder={placeholder}
      summaryFormatter={summaryFormatter}
      keepOpenOnSelect
    />
  );
};
