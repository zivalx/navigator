import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { LineChart as LineChartIcon } from 'lucide-react';
import { api } from '@/lib/api';
import type { PortfolioHistoryPeriod, PortfolioHistoryPoint } from '@/lib/types';
import { InfoTooltip } from '@/components/common/InfoTooltip';
import { EmptyState } from '@/components/common/EmptyState';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';
import { usePortfolio } from '@/contexts/PortfolioContext';

const PERIODS: { value: PortfolioHistoryPeriod; label: string }[] = [
  { value: '1w', label: '1W' },
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
  { value: '6m', label: '6M' },
  { value: '1y', label: '1Y' },
];

const STALE_TIME_MS = 5 * 60 * 1000;

function formatCurrency(value: number, currency: string = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

function formatAxisDate(value: string): string {
  const d = new Date(value);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatTooltipDate(value: string): string {
  const d = new Date(value);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: { payload: PortfolioHistoryPoint }[];
  currency?: string;
}

function ChartTooltip({ active, payload, currency = 'USD' }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  const isPositive = point.pnl >= 0;

  return (
    <div className="rounded-lg border border-border/50 bg-background px-3 py-2 text-xs shadow-xl">
      <div className="font-medium text-foreground mb-1">{formatTooltipDate(point.date)}</div>
      <div className="flex items-center justify-between gap-3 mb-0.5">
        <span className="text-muted-foreground">NAV</span>
        <span className="font-mono font-medium tabular-nums text-foreground">
          {formatCurrency(point.nav, currency)}
        </span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-muted-foreground">P&amp;L</span>
        <span
          className={cn(
            'font-mono font-medium tabular-nums',
            isPositive ? 'text-success' : 'text-destructive'
          )}
        >
          {isPositive ? '+' : ''}
          {formatCurrency(point.pnl, currency)} ({isPositive ? '+' : ''}
          {point.pnl_pct.toFixed(2)}%)
        </span>
      </div>
    </div>
  );
}

export function PerformanceChart() {
  const [period, setPeriod] = useState<PortfolioHistoryPeriod>('3m');
  const { baseCurrency } = usePortfolio();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['portfolio', 'history', period, baseCurrency],
    queryFn: () => api.getPortfolioHistory(period, baseCurrency),
    staleTime: STALE_TIME_MS,
  });

  const points = data?.points ?? [];
  const hasEnoughHistory = points.length >= 2;
  const lastPoint = points[points.length - 1];
  const isPositive = (lastPoint?.pnl ?? 0) >= 0;

  // Color the whole chart (header + area) by the period's overall direction -
  // a single series doesn't need a legend, the sign itself carries the meaning.
  const seriesColor = isPositive ? 'hsl(var(--success))' : 'hsl(var(--destructive))';
  const gradientId = useMemo(
    () => `nav-gradient-${isPositive ? 'up' : 'down'}`,
    [isPositive]
  );

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-4">
        <div>
          <h3 className="text-lg font-semibold flex items-center">
            Portfolio Performance
            <InfoTooltip text="Net asset value over time, based on end-of-day price snapshots. P&L is relative to the start of the selected period." />
          </h3>
          {hasEnoughHistory && lastPoint && (
            <div className="mt-1 flex items-baseline gap-2">
              <span
                className={cn(
                  'font-mono text-xl font-semibold tabular-nums',
                  isPositive ? 'text-success' : 'text-destructive'
                )}
              >
                {isPositive ? '+' : ''}
                {formatCurrency(lastPoint.pnl, baseCurrency)}
              </span>
              <span
                className={cn(
                  'font-mono text-sm font-medium tabular-nums',
                  isPositive ? 'text-success' : 'text-destructive'
                )}
              >
                ({isPositive ? '+' : ''}
                {lastPoint.pnl_pct.toFixed(2)}%)
              </span>
              <span className="text-xs text-muted-foreground">
                over {PERIODS.find((p) => p.value === period)?.label}
              </span>
            </div>
          )}
        </div>

        <ToggleGroup
          type="single"
          value={period}
          onValueChange={(value) => value && setPeriod(value as PortfolioHistoryPeriod)}
          className="justify-start"
        >
          {PERIODS.map((p) => (
            <ToggleGroupItem
              key={p.value}
              value={p.value}
              size="sm"
              className="h-7 px-2.5 text-xs data-[state=on]:bg-primary/10 data-[state=on]:text-primary"
            >
              {p.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-[260px] w-full rounded-lg" />
        </div>
      ) : isError || !hasEnoughHistory ? (
        <EmptyState
          icon={<LineChartIcon className="h-6 w-6" />}
          title="Not enough history yet"
          description={
            isError
              ? 'Could not load portfolio history right now.'
              : 'Once a few days of price history accumulate, your NAV chart will show up here.'
          }
          className="py-10"
        />
      ) : (
        <div className="h-[260px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={seriesColor} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={seriesColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid
                vertical={false}
                stroke="hsl(var(--border))"
                strokeOpacity={0.5}
              />
              <XAxis
                dataKey="date"
                tickFormatter={formatAxisDate}
                axisLine={false}
                tickLine={false}
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                minTickGap={32}
              />
              <YAxis
                dataKey="nav"
                domain={['auto', 'auto']}
                axisLine={false}
                tickLine={false}
                tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                tickFormatter={(value: number) => formatCurrency(value, baseCurrency)}
                width={72}
              />
              <Tooltip
                content={<ChartTooltip currency={baseCurrency} />}
                cursor={{ stroke: 'hsl(var(--border))', strokeWidth: 1 }}
              />
              <Area
                type="monotone"
                dataKey="nav"
                stroke={seriesColor}
                strokeWidth={2}
                fill={`url(#${gradientId})`}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0, fill: seriesColor }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
