import type { PriceAlert, PriceAlertRule } from '@/lib/types';

export function ruleVerb(rule: PriceAlertRule): 'below' | 'above' {
  return rule === 'price_below' ? 'below' : 'above';
}

export function formatMoney(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** Human rule text, e.g. "AAPL below $180.00" */
export function formatAlertHeadline(alert: Pick<PriceAlert, 'symbol' | 'rule' | 'threshold'>): string {
  return `${alert.symbol} ${ruleVerb(alert.rule)} ${formatMoney(alert.threshold)}`;
}

export function isUnacknowledgedTriggered(alert: PriceAlert): boolean {
  return !!alert.triggeredAt && !alert.acknowledgedAt;
}
