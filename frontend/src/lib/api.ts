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
  getHoldings: () => request<any[]>("/portfolio/holdings"),
  createHolding: (data: any) =>
    request<any>("/portfolio/holdings", { method: "POST", body: JSON.stringify(data) }),
  updateHolding: (id: string, data: any) =>
    request<any>(`/portfolio/holdings/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteHolding: (id: string) =>
    request<void>(`/portfolio/holdings/${id}`, { method: "DELETE" }),

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

  // News
  getNews: () => request<any[]>("/news"),
};
