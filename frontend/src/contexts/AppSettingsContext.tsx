import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

/**
 * Auto-refresh interval, in seconds. `0` means "manual only" — no automatic
 * polling; data only updates when the user explicitly triggers a refresh.
 */
export type RefreshIntervalSeconds = 30 | 60 | 300 | 0;

interface AppSettingsState {
  refreshInterval: RefreshIntervalSeconds;
  compactNumbers: boolean;
}

const DEFAULT_SETTINGS: AppSettingsState = {
  refreshInterval: 60,
  compactNumbers: true,
};

const STORAGE_KEY = 'appSettings';

interface AppSettingsContextType extends AppSettingsState {
  setRefreshInterval: (seconds: RefreshIntervalSeconds) => void;
  setCompactNumbers: (compact: boolean) => void;
  /**
   * Ready-to-use value for React Query's `refetchInterval` (or any manual
   * `setInterval`-based polling): a millisecond interval, or `false` when
   * the user has selected "Manual only".
   */
  refreshIntervalMs: number | false;
}

const AppSettingsContext = createContext<AppSettingsContextType | undefined>(undefined);

function loadSettings(): AppSettingsState {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(saved);
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function AppSettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<AppSettingsState>(loadSettings);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const setRefreshInterval = (seconds: RefreshIntervalSeconds) => {
    setSettings(prev => ({ ...prev, refreshInterval: seconds }));
  };

  const setCompactNumbers = (compact: boolean) => {
    setSettings(prev => ({ ...prev, compactNumbers: compact }));
  };

  const refreshIntervalMs: number | false = settings.refreshInterval > 0
    ? settings.refreshInterval * 1000
    : false;

  return (
    <AppSettingsContext.Provider value={{
      ...settings,
      setRefreshInterval,
      setCompactNumbers,
      refreshIntervalMs,
    }}>
      {children}
    </AppSettingsContext.Provider>
  );
}

export function useAppSettings() {
  const context = useContext(AppSettingsContext);
  if (context === undefined) {
    throw new Error('useAppSettings must be used within an AppSettingsProvider');
  }
  return context;
}
