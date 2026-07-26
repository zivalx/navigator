import { useCallback, useEffect, useState } from 'react';
import { defaultIndicatorKeys } from '@/lib/indicatorTypes';

const STORAGE_KEY = 'navigator-indicators';

function loadInitialKeys(): string[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) {
        return parsed.filter((k): k is string => typeof k === 'string');
      }
    }
  } catch {
    // malformed localStorage value — fall back to defaults
  }
  return defaultIndicatorKeys;
}

/**
 * Persists the user's selected indicator keys to localStorage, following the
 * same persistence pattern as MarketCardsContext (read once on mount, write
 * on every change). A plain hook is used instead of a context because the
 * selection is only consumed within the Markets indicators strip and its
 * customize dialog, not across the app.
 */
export function useIndicatorPrefs() {
  const [selectedKeys, setSelectedKeys] = useState<string[]>(loadInitialKeys);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(selectedKeys));
  }, [selectedKeys]);

  const toggleKey = useCallback((key: string) => {
    setSelectedKeys(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    );
  }, []);

  const resetToDefaults = useCallback(() => {
    setSelectedKeys(defaultIndicatorKeys);
  }, []);

  return { selectedKeys, setSelectedKeys, toggleKey, resetToDefaults };
}
