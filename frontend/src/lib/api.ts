import type {
  Currency,
  IndicatorsResponse,
  PortfolioHistoryPeriod,
  PortfolioHistoryResponse,
  PriceAlert,
  PriceAlertStatus,
  CreatePriceAlertPayload,
  UpdatePriceAlertPayload,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return res.json();
}

export const api = {
  // Assets
  getAssets: () => request<any[]>("/assets/"),
  getAssetBySymbol: (symbol: string) => request<any>(`/assets/symbol/${symbol}`),
  createAsset: (data: any) =>
    request<any>("/assets/", { method: "POST", body: JSON.stringify(data) }),

  // Portfolio
  getSummary: (baseCurrency: Currency = "USD") =>
    request<any>(`/portfolio/summary?base_currency=${baseCurrency}`),
  getHoldings: (baseCurrency: Currency = "USD") =>
    request<any[]>(`/portfolio/holdings?base_currency=${baseCurrency}`),
  getGroupedHoldings: (baseCurrency: Currency = "USD") =>
    request<any[]>(`/portfolio/holdings/grouped?base_currency=${baseCurrency}`),
  createHolding: (data: any) =>
    request<any>("/portfolio/holdings", { method: "POST", body: JSON.stringify(data) }),
  updateHolding: (id: string, data: any) =>
    request<any>(`/portfolio/holdings/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteHolding: (id: string) =>
    request<void>(`/portfolio/holdings/${id}`, { method: "DELETE" }),
  getPortfolioHistory: (period: PortfolioHistoryPeriod = "3m", baseCurrency: Currency = "USD") =>
    request<PortfolioHistoryResponse>(`/portfolio/history?period=${period}&base_currency=${baseCurrency}`),

  // Watchlists
  getWatchlists: () => request<any[]>("/watchlist/"),
  createWatchlist: (name: string) =>
    request<any>("/watchlist", { method: "POST", body: JSON.stringify({ name }) }),
  updateWatchlist: (id: string, data: any) =>
    request<any>(`/watchlist/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteWatchlist: (id: string) =>
    request<void>(`/watchlist/${id}`, { method: "DELETE" }),
  getWatchlistItems: (id: string) => request<any[]>(`/watchlist/${id}/items`),
  addWatchlistItem: (watchlistId: string, data: any) =>
    request<any>(`/watchlist/${watchlistId}/items`, { method: "POST", body: JSON.stringify(data) }),
  updateWatchlistItem: (id: string, data: any) =>
    request<any>(`/watchlist/items/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteWatchlistItem: (id: string) =>
    request<void>(`/watchlist/items/${id}`, { method: "DELETE" }),

  // Markets
  getQuote: (symbol: string) => request<any>(`/markets/quote/${symbol}`),
  getQuotes: (symbols: string[]) =>
    request<Record<string, any>>(`/markets/quotes?symbols=${symbols.join(",")}`),
  searchAssets: (query: string) => request<any[]>(`/markets/search?q=${encodeURIComponent(query)}`),

  // Movers
  getTopGainers: (limit = 5) => request<any[]>(`/markets/movers/gainers?limit=${limit}`),
  getTopLosers: (limit = 5) => request<any[]>(`/markets/movers/losers?limit=${limit}`),

  // Indicators (Fear & Greed, VIX, indices, rates, fx, commodities, crypto, breadth)
  getIndicators: (keys?: string[]) =>
    request<IndicatorsResponse>(
      `/markets/indicators${keys && keys.length ? `?keys=${keys.join(",")}` : ""}`
    ),

  // News
  getNews: () => request<any[]>("/news"),

  // Price alerts (docs/superpowers/specs/2026-07-23-price-alerts-design.md)
  getAlerts: (status?: PriceAlertStatus) =>
    request<RawPriceAlert[]>(`/alerts/${status ? `?status=${status}` : ""}`).then((rows) =>
      rows.map(mapPriceAlert)
    ),
  createAlert: (data: CreatePriceAlertPayload) =>
    request<RawPriceAlert>("/alerts/", {
      method: "POST",
      body: JSON.stringify({
        assetId: data.assetId,
        symbol: data.symbol,
        rule: data.rule,
        threshold: data.threshold,
        intent: data.intent,
        trailPercent: data.trailPercent,
        trailAmount: data.trailAmount,
        note: data.note,
      }),
    }).then(mapPriceAlert),
  updateAlert: (id: string, data: UpdatePriceAlertPayload) =>
    request<RawPriceAlert>(`/alerts/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        rule: data.rule,
        threshold: data.threshold,
        intent: data.intent,
        trailPercent: data.trailPercent,
        trailAmount: data.trailAmount,
        note: data.note,
        isActive: data.isActive,
      }),
    }).then(mapPriceAlert),
  deleteAlert: (id: string) => request<void>(`/alerts/${id}`, { method: "DELETE" }),
  acknowledgeAlert: (id: string) =>
    request<RawPriceAlert>(`/alerts/${id}/acknowledge`, { method: "POST" }).then(mapPriceAlert),
};

// The alerts backend follows the codebase's existing camelCase API convention
// (same as watchlist/asset endpoints); only the date fields need string -> Date.
interface RawPriceAlert {
  id: string;
  assetId: string;
  symbol: string;
  name: string;
  rule: PriceAlert["rule"];
  threshold: number | null;
  intent: PriceAlert["intent"];
  trailPercent: number | null;
  trailAmount: number | null;
  highWaterMark: number | null;
  currentStopPrice: number | null;
  note: string | null;
  isActive: boolean;
  createdAt: string;
  triggeredAt: string | null;
  triggeredPrice: number | null;
  acknowledgedAt: string | null;
}

function mapPriceAlert(raw: RawPriceAlert): PriceAlert {
  return {
    id: raw.id,
    assetId: raw.assetId,
    symbol: raw.symbol,
    name: raw.name,
    rule: raw.rule,
    threshold: raw.threshold != null ? Number(raw.threshold) : null,
    intent: raw.intent ?? null,
    trailPercent: raw.trailPercent ?? null,
    trailAmount: raw.trailAmount ?? null,
    highWaterMark: raw.highWaterMark ?? null,
    currentStopPrice: raw.currentStopPrice ?? null,
    note: raw.note ?? undefined,
    isActive: raw.isActive,
    createdAt: new Date(raw.createdAt),
    triggeredAt: raw.triggeredAt ? new Date(raw.triggeredAt) : null,
    triggeredPrice: raw.triggeredPrice ?? null,
    acknowledgedAt: raw.acknowledgedAt ? new Date(raw.acknowledgedAt) : null,
  };
}
