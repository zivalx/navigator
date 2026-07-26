import { AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useAppSettings } from '@/contexts/AppSettingsContext';
import type { MarketIndicator } from '@/lib/types';

interface IndicatorTileProps {
  indicator: MarketIndicator;
  className?: string;
}

function formatValue(value: number, unit: string, compact: boolean): string {
  if (unit === '%') {
    return `${value.toFixed(2)}%`;
  }
  const formatted = compact && Math.abs(value) >= 10000
    ? new Intl.NumberFormat('en-US', {
        notation: 'compact',
        maximumFractionDigits: 1,
      }).format(value)
    : new Intl.NumberFormat('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
  if (unit && unit !== 'points' && unit !== 'index') {
    return `${formatted} ${unit}`;
  }
  return formatted;
}

function formatChange(value: number, unit: string): string {
  const sign = value >= 0 ? '+' : '';
  if (unit === '%') {
    return `${sign}${value.toFixed(2)}pp`;
  }
  return `${sign}${value.toFixed(2)}`;
}

function formatPercent(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function IndicatorTile({ indicator, className }: IndicatorTileProps) {
  const { label, value, unit, change, change_pct, error } = indicator;
  const { compactNumbers } = useAppSettings();
  const hasValue = value !== null && value !== undefined;
  const isPositive = (change ?? 0) >= 0;

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card px-4 py-3 flex flex-col gap-1 transition-colors',
        !hasValue && 'opacity-80',
        className
      )}
    >
      <span className="text-xs font-medium text-muted-foreground line-clamp-1">{label}</span>

      {hasValue ? (
        <>
          <span className="text-lg font-semibold font-mono">{formatValue(value!, unit, compactNumbers)}</span>
          {(change !== null && change !== undefined) && (
            <span
              className={cn(
                'text-xs font-mono inline-flex items-center gap-1',
                isPositive ? 'text-success' : 'text-destructive'
              )}
            >
              <span className="text-[0.8em]">{isPositive ? '▲' : '▼'}</span>
              {formatChange(change, unit)}
              {change_pct !== null && change_pct !== undefined && (
                <span className="text-muted-foreground">({formatPercent(change_pct)})</span>
              )}
            </span>
          )}
        </>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex items-center gap-1 text-lg font-semibold font-mono text-muted-foreground cursor-help">
              —
              <AlertCircle className="h-3.5 w-3.5 text-muted-foreground/60" />
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-[220px] text-xs">
            {error ?? 'No data available'}
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}
