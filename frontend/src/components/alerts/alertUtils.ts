import type { PriceAlert, PriceAlertRule } from '@/lib/types';
import { formatMoney } from '@/lib/utils';

export function ruleVerb(rule: PriceAlertRule): 'below' | 'above' | 'trailing stop' {
  if (rule === 'trailing_stop') return 'trailing stop';
  return rule === 'price_below' ? 'below' : 'above';
}

/** "8%" or "$15.00" — however the trail was defined. */
export function formatTrail(alert: Pick<PriceAlert, 'trailPercent' | 'trailAmount' | 'currency'>): string {
  if (alert.trailPercent != null) return `${alert.trailPercent}%`;
  if (alert.trailAmount != null) return formatMoney(alert.trailAmount, alert.currency || 'USD');
  return '';
}

/** Human rule text, e.g. "AAPL below $180.00" or "AAPL trailing stop 8%" */
export function formatAlertHeadline(
  alert: Pick<PriceAlert, 'symbol' | 'rule' | 'threshold' | 'trailPercent' | 'trailAmount' | 'currency'>
): string {
  if (alert.rule === 'trailing_stop') {
    return `${alert.symbol} trailing stop ${formatTrail(alert)}`;
  }
  return `${alert.symbol} ${ruleVerb(alert.rule)} ${formatMoney(alert.threshold ?? 0, alert.currency || 'USD')}`;
}

export function isUnacknowledgedTriggered(alert: PriceAlert): boolean {
  return !!alert.triggeredAt && !alert.acknowledgedAt;
}
