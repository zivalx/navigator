import { AlertTriangle } from 'lucide-react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { UNAVAILABLE_REASON_LABELS, unavailableReasonLabel } from '@/lib/utils';

// Display order of the reasons in the headline and the per-reason lines.
const REASON_ORDER = Object.keys(UNAVAILABLE_REASON_LABELS);

/**
 * Names the holdings the dashboard totals leave out because they have no
 * base-currency value, so they never silently disappear from the portfolio.
 */
export function ExcludedHoldingsBanner() {
  const { excludedHoldings } = usePortfolio();
  if (excludedHoldings.length === 0) return null;

  const byReason = new Map<string, string[]>();
  for (const { symbol, reason } of excludedHoldings) {
    byReason.set(reason, [...(byReason.get(reason) ?? []), symbol]);
  }
  const reasons = Array.from(byReason.keys()).sort((a, b) => {
    const ia = REASON_ORDER.indexOf(a);
    const ib = REASON_ORDER.indexOf(b);
    return (ia === -1 ? REASON_ORDER.length : ia) - (ib === -1 ? REASON_ORDER.length : ib);
  });

  const count = excludedHoldings.length;
  const labels = reasons.map(unavailableReasonLabel).join(' / ');

  return (
    <div role="alert" className="flex gap-3 rounded-xl border border-warning/20 bg-warning/5 p-4">
      <AlertTriangle className="h-4 w-4 shrink-0 text-warning mt-0.5" />
      <div className="space-y-1 text-sm">
        <p className="font-medium">
          Totals exclude {count} {count === 1 ? 'holding' : 'holdings'} ({labels})
        </p>
        {reasons.map(reason => (
          <p key={reason} className="text-muted-foreground">
            {unavailableReasonLabel(reason)}:{' '}
            <span className="font-mono text-foreground">{byReason.get(reason)!.join(', ')}</span>
          </p>
        ))}
      </div>
    </div>
  );
}
