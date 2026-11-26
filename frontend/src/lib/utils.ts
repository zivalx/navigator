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

/**
 * Readable names for Yahoo's exchange codes ("NYQ" -> "NYSE", "TLV" -> "Tel Aviv").
 * Also folds Yahoo's long venue names ("NasdaqGS") onto the same short label.
 */
const EXCHANGE_LABELS: Record<string, string> = {
  // US
  NYQ: "NYSE", NYSE: "NYSE",
  NMS: "Nasdaq", NGM: "Nasdaq", NCM: "Nasdaq", NAS: "Nasdaq", NIM: "Nasdaq",
  NASDAQ: "Nasdaq", NASDAQGS: "Nasdaq", NASDAQGM: "Nasdaq", NASDAQCM: "Nasdaq",
  ASE: "NYSE American", PCX: "NYSE Arca", NYSEARCA: "NYSE Arca", BTS: "Cboe",
  PNK: "OTC", OQB: "OTC", OQX: "OTC",
  SNP: "S&P", DJI: "Dow Jones", WCB: "Cboe",
  // Israel
  TLV: "Tel Aviv",
  // Europe
  LSE: "London", IOB: "London", GER: "XETRA", FRA: "Frankfurt", PAR: "Paris",
  AMS: "Amsterdam", MIL: "Milan", MCE: "Madrid", EBS: "Swiss", STO: "Stockholm",
  CPH: "Copenhagen", HEL: "Helsinki", OSL: "Oslo", BRU: "Brussels", LIS: "Lisbon",
  VIE: "Vienna",
  // Asia-Pacific and others
  TYO: "Tokyo", JPX: "Tokyo", HKG: "Hong Kong", SHH: "Shanghai", SHZ: "Shenzhen",
  KSC: "Korea", TAI: "Taiwan", SES: "Singapore", NSI: "NSE India", BSE: "BSE India",
  ASX: "ASX", TOR: "Toronto",
  CCC: "Crypto",
};

/**
 * Short readable exchange name for a Yahoo code or venue name. Unknown codes fall
 * back to `fallback` (e.g. Yahoo's own display name) and then to the raw code.
 */
export function exchangeLabel(code?: string | null, fallback?: string | null): string {
  const raw = (code ?? "").trim();
  if (!raw) return (fallback ?? "").trim();
  return EXCHANGE_LABELS[raw.toUpperCase().replace(/\s+/g, "")] ?? (fallback?.trim() || raw);
}

/** Index levels are points, not money: format without a currency symbol. */
export function formatQuotePrice(value: number, currency: string = "USD", assetType?: string): string {
  if (assetType === "index") {
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  }
  return formatMoney(value, currency);
}
