import { useQuery } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { BarChart3 } from 'lucide-react';
import { api } from '@/lib/api';
import type { DailyPnLPoint, PortfolioHistoryPeriod } from '@/lib/types';
import { EmptyState } from '@/components/common/EmptyState';
import { Skeleton } from '@/components/ui/skeleton';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { formatMoney } from '@/lib/utils';

const STALE_TIME_MS = 5 * 60 * 1000;

function formatAxisDate(value: string): string {
  return new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

interface TooltipProps {
  active?: boolean;
  payload?: { payload: DailyPnLPoint }[];
  currency?: string;
}

function DailyTooltip({ active, payload, currency = 'USD' }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const positive = p.dailyPnL >= 0;
  return (
    <div className="rounded-lg border border-border/50 bg-background px-3 py-2 text-xs shadow-xl">
      <div className="font-medium text-foreground mb-1">
        {new Date(p.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
      </div>
      <div className="flex items-center justify-between gap-3 mb-0.5">
        <span className="text-muted-foreground">Daily P&amp;L</span>
        <span className={positive ? 'font-mono font-medium text-success' : 'font-mono font-medium text-destructive'}>
          {formatMoney(p.dailyPnL, currency, true)} ({positive ? '+' : ''}{p.dailyPnLPercent.toFixed(2)}%)
        </span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-muted-foreground">NAV</span>
        <span className="font-mono font-medium tabular-nums text-foreground">{formatMoney(p.nav, currency)}</span>
      </div>
    </div>
  );
}

export function DailyPnLChart({ period }: { period: PortfolioHistoryPeriod }) {
  const { baseCurrency } = usePortfolio();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['portfolio', 'daily-pnl', period, baseCurrency],
    queryFn: () => api.getDailyPnL(period, baseCurrency),
    staleTime: STALE_TIME_MS,
  });

  const points = data?.points ?? [];

  if (isLoading) return <Skeleton className="h-[260px] w-full rounded-lg" />;
  if (isError || points.length === 0) {
    return (
      <EmptyState
        icon={<BarChart3 className="h-6 w-6" />}
        title="No daily P&L yet"
        description={
          isError
            ? 'Could not load the daily P&L series right now.'
            : 'Once a couple of days of end-of-day snapshots accumulate, daily gains and losses will appear here.'
        }
        className="py-10"
      />
    );
  }

  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="hsl(var(--border))" strokeOpacity={0.5} />
          <XAxis
            dataKey="date"
            tickFormatter={formatAxisDate}
            axisLine={false}
            tickLine={false}
            tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
            minTickGap={32}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
            tickFormatter={(v: number) => formatMoney(v, baseCurrency)}
            width={72}
          />
          <Tooltip
            content={<DailyTooltip currency={baseCurrency} />}
            cursor={{ fill: 'hsl(var(--muted))', fillOpacity: 0.3 }}
          />
          <Bar dataKey="dailyPnL" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {points.map((p) => (
              <Cell
                key={p.date}
                fill={p.dailyPnL >= 0 ? 'hsl(var(--success))' : 'hsl(var(--destructive))'}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
