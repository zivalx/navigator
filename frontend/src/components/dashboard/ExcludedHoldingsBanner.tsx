import { AlertTriangle } from 'lucide-react';
import { usePortfolio, ExcludedHolding } from '@/contexts/PortfolioContext';
import { UnavailableReason } from '@/lib/types';
import { UNAVAILABLE_REASON_LABELS, unavailableReasonLabel } from '@/lib/utils';

// Display order of the reasons in the headline and the per-reason lines.
const REASON_ORDER = Object.keys(UNAVAILABLE_REASON_LABELS) as UnavailableReason[];

/** "TEVA.TA", or "TEVA.TA (1 of 2 lots)" when some of its lots still count. */
function symbolLabel({ symbol, excludedLots, totalLots }: ExcludedHolding): string {
  return excludedLots < totalLots ? `${symbol} (${excludedLots} of ${totalLots} lots)` : symbol;
}

/**
 * Names the holdings the dashboard totals leave out because they have no
 * base-currency value, so they never silently disappear from the portfolio.
 */
export function ExcludedHoldingsBanner() {
  const { excludedHoldings } = usePortfolio();
  if (excludedHoldings.length === 0) return null;

  const byReason = new Map<UnavailableReason, string[]>();
  for (const h of excludedHoldings) {
    byReason.set(h.reason, [...(byReason.get(h.reason) ?? []), symbolLabel(h)]);
  }
  const reasons = REASON_ORDER.filter(r => byReason.has(r));

  // The API's "holdings" are lots: count every excluded lot.
  const count = excludedHoldings.reduce((sum, h) => sum + h.excludedLots, 0);
  const labels = reasons.map(unavailableReasonLabel).join(' / ');

  return (
    <div role="status" className="flex gap-3 rounded-xl border border-warning/20 bg-warning/5 p-4">
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
