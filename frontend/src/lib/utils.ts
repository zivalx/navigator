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
