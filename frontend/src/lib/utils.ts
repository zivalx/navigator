import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format a number as currency with the correct symbol for the given currency
 * code (e.g. "$1,234.00", "€1,234.00"). Use instead of a hardcoded "$" prefix
 * so non-USD base/cost currencies render their own symbol. `signed` prefixes a
 * "+" for positive values (for P&L cells).
 */
export function formatMoney(
  value: number,
  currency: string = "USD",
  signed = false,
): string {
  // Normalize -0 to 0 so it never renders as "+-$0.00".
  const v = value === 0 ? 0 : value;
  const formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v);
  return signed && v >= 0 ? `+${formatted}` : formatted;
}

/** Currency symbol for a code (e.g. "USD" -> "$", "ILS" -> "₪"); falls back to the code. */
export function currencySymbol(code: string): string {
  try {
    const part = new Intl.NumberFormat("en-US", { style: "currency", currency: code })
      .formatToParts(0)
      .find((p) => p.type === "currency");
    return part?.value ?? code;
  } catch {
    return code;
  }
}

/** Short user-facing label for why a holding has no base-currency value. */
export const UNAVAILABLE_REASON_LABELS: Record<string, string> = {
  fx_unavailable: "no exchange rate",
  lot_currency_check: "check lot currency",
  price_unavailable: "no price",
};

export function unavailableReasonLabel(reason?: string | null): string {
  return (reason && UNAVAILABLE_REASON_LABELS[reason]) || "value unavailable";
}
