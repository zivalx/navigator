import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { api } from '@/lib/api';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { FearGreedGauge } from './FearGreedGauge';
import { IndicatorTile } from './IndicatorTile';
import { IndicatorsCustomizeDialog } from './IndicatorsCustomizeDialog';
import { useIndicatorPrefs } from '@/hooks/useIndicatorPrefs';
import { useAppSettings } from '@/contexts/AppSettingsContext';
import { indicatorMetaByKey, sentimentKeys } from '@/lib/indicatorTypes';
import type { MarketIndicator } from '@/lib/types';

function GaugeSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-card p-4 flex flex-col items-center gap-3 w-full max-w-[220px]">
      <LoadingSkeleton variant="text" height={14} width="60%" />
      <LoadingSkeleton variant="rectangular" height={90} width="100%" className="rounded-t-full" />
      <LoadingSkeleton variant="text" height={24} width="30%" />
    </div>
  );
}

function TileSkeleton() {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 space-y-2">
      <LoadingSkeleton variant="text" height={12} width="70%" />
      <LoadingSkeleton variant="text" height={20} width="50%" />
    </div>
  );
}

export function IndicatorsStrip() {
  const { selectedKeys, toggleKey, resetToDefaults } = useIndicatorPrefs();
  const { refreshIntervalMs } = useAppSettings();

  const sortedKeys = useMemo(() => [...selectedKeys].sort(), [selectedKeys]);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['market-indicators', sortedKeys],
    queryFn: () => api.getIndicators(selectedKeys),
    refetchInterval: refreshIntervalMs,
    enabled: selectedKeys.length > 0,
  });

  const indicatorsByKey = useMemo(() => {
    const map: Record<string, MarketIndicator> = {};
    for (const ind of data?.indicators ?? []) {
      map[ind.key] = ind;
    }
    return map;
  }, [data]);

  const selectedGaugeKeys = selectedKeys.filter(k => sentimentKeys.includes(k));
  const selectedTileKeys = selectedKeys.filter(k => !sentimentKeys.includes(k));

  const showLoading = isLoading && selectedKeys.length > 0;
  const showEmpty = selectedKeys.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Market Indicators</h2>
        <IndicatorsCustomizeDialog
          selectedKeys={selectedKeys}
          onToggle={toggleKey}
          onReset={resetToDefaults}
        />
      </div>

      {showEmpty && (
        <div className="text-center py-8 border border-dashed rounded-xl">
          <p className="text-muted-foreground mb-1">No indicators selected</p>
          <p className="text-sm text-muted-foreground/70">
            Use Customize to choose which indicators to show.
          </p>
        </div>
      )}

      {!showEmpty && isError && (
        <div className="rounded-xl border border-destructive/20 bg-destructive/5 p-4 flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-destructive shrink-0" />
          <p className="text-sm text-muted-foreground">
            Couldn't load market indicators. They'll retry automatically.
          </p>
        </div>
      )}

      {!showEmpty && !isError && (
        <>
          {selectedGaugeKeys.length > 0 && (
            <div className="flex flex-wrap gap-4">
              {showLoading
                ? selectedGaugeKeys.map(key => <GaugeSkeleton key={key} />)
                : selectedGaugeKeys.map(key => {
                    const meta = indicatorMetaByKey[key];
                    const indicator = indicatorsByKey[key];
                    return (
                      <FearGreedGauge
                        key={key}
                        label={indicator?.label ?? meta?.label ?? key}
                        value={indicator?.value ?? null}
                        rating={indicator?.rating ?? null}
                        change={indicator?.change ?? null}
                        error={indicator?.error ?? null}
                        className="flex-1 min-w-[200px]"
                      />
                    );
                  })}
            </div>
          )}

          {selectedTileKeys.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
              {showLoading
                ? selectedTileKeys.map(key => <TileSkeleton key={key} />)
                : selectedTileKeys.map(key => {
                    const meta = indicatorMetaByKey[key];
                    const indicator: MarketIndicator = indicatorsByKey[key] ?? {
                      key,
                      label: meta?.label ?? key,
                      category: meta?.category ?? 'index',
                      value: null,
                      unit: '',
                      rating: null,
                      change: null,
                      change_pct: null,
                      source: '',
                      error: 'No data available',
                    };
                    return <IndicatorTile key={key} indicator={indicator} />;
                  })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
