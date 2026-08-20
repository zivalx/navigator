import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import {
  Asset, HoldingLot, WatchlistItem, Watchlist, NewsItem, Currency,
  HoldingWithAsset, WatchlistItemWithAsset, PortfolioSummary, Alert, MarketMover, GroupedHolding
} from '@/lib/types';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useAppSettings } from '@/contexts/AppSettingsContext';

interface PortfolioContextType {
  // Data
  assets: Asset[];
  holdings: HoldingLot[];
  watchlists: Watchlist[];
  watchlistItems: WatchlistItem[];
  activeWatchlistId: string | null;
  news: NewsItem[];
  alerts: Alert[];

  // Computed
  holdingsWithAssets: HoldingWithAsset[];
  groupedHoldings: GroupedHolding[];
  watchlistWithAssets: WatchlistItemWithAsset[];
  watchedAssetIds: Set<string>;
  portfolioSummary: PortfolioSummary;
  topHoldings: GroupedHolding[];
  topMovers: { gainers: MarketMover[]; losers: MarketMover[] };

  // Settings
  baseCurrency: Currency;
  setBaseCurrency: (currency: Currency) => void;
  lastUpdated: Date;
  isLoading: boolean;

  // Actions
  refreshPrices: () => void;
  addHolding: (holding: Omit<HoldingLot, 'id' | 'createdAt'>) => void;
  addCustomAsset: (asset: Omit<Asset, 'id'>, holding: { quantity: number; avgCost: number; currentPrice: number; accountName: string; purchaseDate: Date }) => void;
  removeHolding: (id: string) => void;
  updateHolding: (id: string, updates: Partial<HoldingLot>) => void;

  // Watchlist actions
  setActiveWatchlistId: (id: string | null) => void;
  createWatchlist: (name: string) => void;
  renameWatchlist: (id: string, name: string) => void;
  deleteWatchlist: (id: string) => void;
  addToWatchlist: (assetId: string, watchlistId?: string, notes?: string, targetPrice?: number) => void;
  updateWatchlistItem: (id: string, updates: { notes?: string; targetPrice?: number }) => void;
  removeFromWatchlist: (id: string) => void;

  importHoldings: (holdings: Omit<HoldingLot, 'id' | 'createdAt'>[]) => void;
  searchAssets: (query: string) => Asset[];
}

const PortfolioContext = createContext<PortfolioContextType | undefined>(undefined);

export function PortfolioProvider({ children }: { children: ReactNode }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [holdingsWithAssets, setHoldingsWithAssets] = useState<HoldingWithAsset[]>([]);
  const [holdings, setHoldings] = useState<HoldingLot[]>([]);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>([]);
  const [watchedAssetIds, setWatchedAssetIds] = useState<Set<string>>(new Set());
  const [activeWatchlistId, setActiveWatchlistId] = useState<string | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [baseCurrency, setBaseCurrency] = useState<Currency>(() => {
    const saved = localStorage.getItem('baseCurrency');
    return (saved as Currency) || 'USD';
  });
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const { refreshIntervalMs } = useAppSettings();

  // Persist base currency selection
  useEffect(() => {
    localStorage.setItem('baseCurrency', baseCurrency);
  }, [baseCurrency]);

  // Fetch all data from API
  const fetchData = useCallback(async () => {
    try {
      setIsLoading(true);
      const [assetsData, holdingsData, watchlistsData] = await Promise.all([
        api.getAssets(),
        api.getHoldings(baseCurrency).catch(() => []),
        api.getWatchlists().catch(() => []),
      ]);

      setAssets(assetsData.map((a: any) => ({
        id: a.id,
        symbol: a.symbol,
        name: a.name,
        exchange: a.exchange,
        currency: a.currency?.toUpperCase() || 'USD',
        assetType: a.assetType || a.asset_type,
        marketRegion: a.marketRegion || a.market_region || 'US',
        providerIds: a.providerIds || a.provider_ids,
      })));

      // Holdings from API already include asset + price data
      const mappedHoldings: HoldingWithAsset[] = holdingsData.map((h: any) => ({
        id: h.id,
        assetId: h.assetId || h.asset_id,
        quantity: h.quantity,
        avgCost: h.avgCost ?? h.avg_cost,
        costCurrency: (h.costCurrency || h.cost_currency || 'USD').toUpperCase(),
        accountName: h.accountName || h.account_name || '',
        tags: h.tags || [],
        purchaseDate: new Date(h.purchaseDate || h.purchase_date),
        createdAt: new Date(h.createdAt || h.created_at || Date.now()),
        asset: h.asset ? {
          id: h.asset.id,
          symbol: h.asset.symbol,
          name: h.asset.name,
          exchange: h.asset.exchange,
          currency: (h.asset.currency || 'USD').toUpperCase() as Currency,
          assetType: h.asset.assetType || h.asset.asset_type,
          marketRegion: h.asset.marketRegion || h.asset.market_region || 'US',
        } : { id: h.assetId || h.asset_id, symbol: 'UNKNOWN', name: 'Unknown', currency: 'USD' as Currency, assetType: 'stock' as const, marketRegion: 'US' as const },
        currentPrice: h.currentPrice ?? h.current_price,
        priceChange: h.priceChange ?? h.price_change,
        priceChangePercent: h.priceChangePercent ?? h.price_change_percent,
        avgCostBase: h.avgCostBase ?? h.avg_cost_base,
        marketValue: h.marketValue ?? h.market_value,
        unrealizedPnL: h.unrealizedPnL ?? h.unrealized_pnl,
        unrealizedPnLPercent: h.unrealizedPnLPercent ?? h.unrealized_pnl_percent,
      }));

      setHoldingsWithAssets(mappedHoldings);
      setHoldings(mappedHoldings.map(h => ({
        id: h.id,
        assetId: h.assetId,
        quantity: h.quantity,
        avgCost: h.avgCost,
        costCurrency: h.costCurrency,
        accountName: h.accountName,
        tags: h.tags,
        purchaseDate: h.purchaseDate,
        createdAt: h.createdAt,
      })));

      // Watchlists
      const mappedWatchlists: Watchlist[] = watchlistsData.map((w: any) => ({
        id: w.id,
        name: w.name,
        createdAt: new Date(w.createdAt || w.created_at || Date.now()),
      }));
      setWatchlists(mappedWatchlists);
      if (mappedWatchlists.length > 0 && !activeWatchlistId) {
        setActiveWatchlistId(mappedWatchlists[0].id);
      }

      // Build set of all watched asset IDs across all watchlists
      const allWatchedIds = new Set<string>();
      await Promise.all(mappedWatchlists.map(async (wl) => {
        try {
          const items = await api.getWatchlistItems(wl.id);
          items.forEach((item: any) => {
            const aid = item.assetId || item.asset_id;
            if (aid) allWatchedIds.add(aid);
          });
        } catch { /* skip failed lists */ }
      }));
      setWatchedAssetIds(allWatchedIds);

      setLastUpdated(new Date());
    } catch (err) {
      console.error('Failed to fetch portfolio data:', err);
      setAlerts(prev => [...prev, {
        id: `err_${Date.now()}`,
        type: 'api_error' as const,
        message: `Failed to load data: ${err}`,
        timestamp: new Date(),
      }]);
    } finally {
      setIsLoading(false);
    }
  }, [baseCurrency]);

  // Fetch watchlist items when active watchlist changes
  useEffect(() => {
    if (!activeWatchlistId) {
      setWatchlistItems([]);
      return;
    }
    api.getWatchlistItems(activeWatchlistId)
      .then(items => {
        setWatchlistItems(items.map((item: any) => ({
          id: item.id,
          watchlistId: item.watchlistId || item.watchlist_id,
          assetId: item.assetId || item.asset_id,
          notes: item.notes,
          targetPrice: item.targetPrice || item.target_price,
          createdAt: new Date(item.createdAt || item.created_at || Date.now()),
          currentPrice: item.currentPrice ?? item.current_price,
          priceChange: item.priceChange ?? item.price_change,
          priceChangePercent: item.priceChangePercent ?? item.price_change_percent,
          change1d: item.change1d ?? item.change_1d,
          change1m: item.change1m ?? item.change_1m,
          change6m: item.change6m ?? item.change_6m,
          asset: item.asset ? {
            id: item.asset.id,
            symbol: item.asset.symbol,
            name: item.asset.name,
            exchange: item.asset.exchange,
            currency: (item.asset.currency || 'USD').toUpperCase(),
            assetType: item.asset.assetType || item.asset.asset_type,
            marketRegion: item.asset.marketRegion || item.asset.market_region || 'US',
          } : undefined,
        })));
      })
      .catch((err) => {
        console.error('Failed to load watchlist items:', err);
        setWatchlistItems([]);
      });
  }, [activeWatchlistId]);

  // Initial load
  useEffect(() => { fetchData(); }, [fetchData]);

  // Add weights to holdings
  const totalNav = holdingsWithAssets.reduce((sum, h) => sum + (h.marketValue || 0), 0);
  const holdingsWithWeights = holdingsWithAssets.map(h => ({
    ...h,
    weight: totalNav > 0 && h.marketValue ? (h.marketValue / totalNav) * 100 : 0,
  }));

  // Base-currency cost basis per lot. Falls back to the native avgCost only
  // if the backend didn't supply avgCostBase (e.g. an older payload) — never
  // sum the native avgCost across mixed cost currencies.
  const costBaseOf = (h: HoldingWithAsset) =>
    h.quantity * (h.avgCostBase ?? h.avgCost);

  // Portfolio summary
  const portfolioSummary: PortfolioSummary = useMemo(() => {
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);

    // Daily P&L only counts lots held at the previous close. A lot bought
    // today didn't earn today's since-prev-close move (it was bought at
    // today's price) — counting it would inflate the day's gain; its P&L vs
    // its actual entry is captured in unrealized P&L instead. priceChange and
    // currentPrice are already base-currency (converted by the backend), so
    // `currentPrice - priceChange` is the base-currency previous close.
    let dailyPnL = 0;
    let prevNav = 0; // previous-close value of the lots that count above
    for (const h of holdingsWithWeights) {
      const heldAtPrevClose = new Date(h.purchaseDate) < startOfToday;
      if (heldAtPrevClose && h.priceChange != null && h.currentPrice != null) {
        dailyPnL += h.quantity * h.priceChange;
        prevNav += h.quantity * (h.currentPrice - h.priceChange);
      }
    }

    const totalCost = holdingsWithWeights.reduce((sum, h) => sum + costBaseOf(h), 0);
    const totalUnrealizedPnL = holdingsWithWeights.reduce((sum, h) => sum + (h.unrealizedPnL || 0), 0);

    return {
      totalNav,
      baseCurrency,
      dailyPnL,
      dailyPnLPercent: prevNav > 0 ? (dailyPnL / prevNav) * 100 : 0,
      totalCost,
      totalUnrealizedPnL,
      totalUnrealizedPnLPercent: totalCost > 0 ? (totalUnrealizedPnL / totalCost) * 100 : 0,
      lastUpdated,
    };
  }, [holdingsWithWeights, totalNav, baseCurrency, lastUpdated]);

  // Grouped holdings — one entry per asset, aggregating all its lots
  const groupedHoldings: GroupedHolding[] = useMemo(() => {
    const groups = new Map<string, HoldingWithAsset[]>();
    holdingsWithWeights.forEach(h => {
      const existing = groups.get(h.assetId) || [];
      groups.set(h.assetId, [...existing, h]);
    });

    return Array.from(groups.entries()).map(([assetId, lots]) => {
      const asset = lots[0].asset;
      const totalQuantity = lots.reduce((sum, l) => sum + l.quantity, 0);
      // Base-currency cost so the position's avgCost/% line up with its
      // base-currency market value (mixed-currency lots don't corrupt it).
      const totalCost = lots.reduce((sum, l) => sum + costBaseOf(l), 0);
      const avgCost = totalQuantity > 0 ? totalCost / totalQuantity : 0;
      const marketValue = lots.reduce((sum, l) => sum + (l.marketValue || 0), 0);
      const unrealizedPnL = lots.reduce((sum, l) => sum + (l.unrealizedPnL || 0), 0);
      const weight = lots.reduce((sum, l) => sum + (l.weight || 0), 0);

      return {
        assetId,
        asset,
        lots: lots.sort((a, b) => new Date(b.purchaseDate).getTime() - new Date(a.purchaseDate).getTime()),
        totalQuantity,
        avgCost,
        currentPrice: lots[0].currentPrice,
        priceChange: lots[0].priceChange,
        priceChangePercent: lots[0].priceChangePercent,
        marketValue,
        unrealizedPnL,
        unrealizedPnLPercent: totalCost > 0 ? (unrealizedPnL / totalCost) * 100 : 0,
        weight,
      };
    });
  }, [holdingsWithWeights]);

  // Top holdings by weight — grouped per asset, so a symbol held across
  // multiple lots (e.g. AAPL in two accounts) shows as one position.
  const topHoldings = useMemo(() =>
    [...groupedHoldings].sort((a, b) => (b.weight || 0) - (a.weight || 0)).slice(0, 5),
    [groupedHoldings]
  );

  // Top movers — fetched from real market data API
  const [topMovers, setTopMovers] = useState<{ gainers: MarketMover[]; losers: MarketMover[] }>({ gainers: [], losers: [] });

  useEffect(() => {
    Promise.all([
      api.getTopGainers(5).catch(() => []),
      api.getTopLosers(5).catch(() => []),
    ]).then(([gainers, losers]) => {
      setTopMovers({
        gainers: gainers.map((m: any) => ({
          asset: { id: m.symbol, symbol: m.symbol, name: m.name || '', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
          price: m.price || 0,
          change: m.change || 0,
          changePercent: m.changePercent || 0,
        })),
        losers: losers.map((m: any) => ({
          asset: { id: m.symbol, symbol: m.symbol, name: m.name || '', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
          price: m.price || 0,
          change: m.change || 0,
          changePercent: m.changePercent || 0,
        })),
      });
    });
  }, [lastUpdated]);

  // Watchlist items with assets — prices come from the API response
  const watchlistWithAssets: WatchlistItemWithAsset[] = useMemo(() => {
    return watchlistItems.map((item: any) => {
      const asset = item.asset || assets.find(a => a.id === item.assetId) || { id: item.assetId, symbol: 'UNKNOWN', name: 'Unknown', currency: 'USD' as Currency, assetType: 'stock' as const, marketRegion: 'US' as const };
      return {
        ...item,
        asset,
        currentPrice: item.currentPrice,
        priceChange: item.priceChange,
        priceChangePercent: item.priceChangePercent,
        change1d: item.change1d,
        change1m: item.change1m,
        change6m: item.change6m,
      };
    });
  }, [watchlistItems, assets]);

  // Actions
  const refreshPrices = useCallback(() => { fetchData(); }, [fetchData]);

  const addHolding = useCallback(async (holding: Omit<HoldingLot, 'id' | 'createdAt'>) => {
    try {
      await api.createHolding({
        asset_id: holding.assetId,
        quantity: holding.quantity,
        avg_cost: holding.avgCost,
        cost_currency: holding.costCurrency,
        account_name: holding.accountName,
        tags: holding.tags || [],
        purchase_date: holding.purchaseDate instanceof Date ? holding.purchaseDate.toISOString() : holding.purchaseDate,
      });
      fetchData();
    } catch (err) {
      toast.error('Failed to add holding');
    }
  }, [fetchData]);

  const addCustomAsset = useCallback(async (
    assetData: Omit<Asset, 'id'>,
    holdingData: { quantity: number; avgCost: number; currentPrice: number; accountName: string; purchaseDate: Date }
  ) => {
    try {
      const newAsset = await api.createAsset({
        symbol: assetData.symbol,
        name: assetData.name,
        exchange: assetData.exchange,
        currency: assetData.currency,
        asset_type: assetData.assetType,
        market_region: assetData.marketRegion,
        provider_ids: assetData.providerIds || {},
      });
      await api.createHolding({
        asset_id: newAsset.id,
        quantity: holdingData.quantity,
        avg_cost: holdingData.avgCost,
        cost_currency: assetData.currency,
        account_name: holdingData.accountName,
        tags: [],
        purchase_date: holdingData.purchaseDate.toISOString(),
      });
      fetchData();
    } catch (err) {
      toast.error('Failed to add custom asset');
    }
  }, [fetchData]);

  const removeHolding = useCallback(async (id: string) => {
    try {
      await api.deleteHolding(id);
      fetchData();
    } catch (err) {
      toast.error('Failed to remove holding');
    }
  }, [fetchData]);

  const updateHolding = useCallback(async (id: string, updates: Partial<HoldingLot>) => {
    try {
      const payload: Record<string, any> = {};
      if (updates.quantity !== undefined) payload.quantity = updates.quantity;
      if (updates.avgCost !== undefined) payload.avgCost = updates.avgCost;
      if (updates.costCurrency !== undefined) payload.costCurrency = updates.costCurrency;
      if (updates.accountName !== undefined) payload.accountName = updates.accountName;
      if (updates.tags !== undefined) payload.tags = updates.tags;
      if (updates.purchaseDate !== undefined) {
        payload.purchaseDate = updates.purchaseDate instanceof Date
          ? updates.purchaseDate.toISOString()
          : updates.purchaseDate;
      }
      await api.updateHolding(id, payload);
      fetchData();
    } catch (err) {
      toast.error('Failed to update holding');
    }
  }, [fetchData]);

  // Watchlist actions
  const createWatchlist = useCallback(async (name: string) => {
    try {
      const wl = await api.createWatchlist(name);
      setWatchlists(prev => [...prev, { id: wl.id, name: wl.name, createdAt: new Date() }]);
      setActiveWatchlistId(wl.id);
    } catch (err) {
      toast.error('Failed to create watchlist');
    }
  }, []);

  const renameWatchlist = useCallback(async (id: string, name: string) => {
    try {
      await api.updateWatchlist(id, { name });
      setWatchlists(prev => prev.map(wl => wl.id === id ? { ...wl, name } : wl));
    } catch (err) {
      toast.error('Failed to rename watchlist');
    }
  }, []);

  const deleteWatchlist = useCallback(async (id: string) => {
    try {
      await api.deleteWatchlist(id);
      setWatchlists(prev => prev.filter(wl => wl.id !== id));
      if (activeWatchlistId === id) {
        const remaining = watchlists.filter(wl => wl.id !== id);
        setActiveWatchlistId(remaining[0]?.id || null);
      }
    } catch (err) {
      toast.error('Failed to delete watchlist');
    }
  }, [activeWatchlistId, watchlists]);

  const addToWatchlist = useCallback(async (assetId: string, watchlistId?: string, notes?: string, targetPrice?: number) => {
    const targetId = watchlistId || activeWatchlistId;
    if (!targetId) return;
    try {
      await api.addWatchlistItem(targetId, {
        asset_id: assetId,
        notes,
        target_price: targetPrice,
      });
      // Track in watched set
      setWatchedAssetIds(prev => new Set(prev).add(assetId));
      // Refresh watchlist items
      const items = await api.getWatchlistItems(targetId);
      setWatchlistItems(items.map((item: any) => ({
        id: item.id,
        watchlistId: item.watchlistId || item.watchlist_id,
        assetId: item.assetId || item.asset_id,
        notes: item.notes,
        targetPrice: item.targetPrice || item.target_price,
        createdAt: new Date(item.createdAt || item.created_at || Date.now()),
      })));
    } catch (err) {
      toast.error('Failed to add to watchlist');
    }
  }, [activeWatchlistId]);

  const updateWatchlistItem = useCallback(async (id: string, updates: { notes?: string; targetPrice?: number }) => {
    try {
      await api.updateWatchlistItem(id, updates);
      setWatchlistItems(prev => prev.map(w => w.id === id ? { ...w, ...updates } : w));
    } catch (err) {
      toast.error('Failed to update watchlist item');
    }
  }, []);

  const removeFromWatchlist = useCallback(async (id: string) => {
    try {
      await api.deleteWatchlistItem(id);
      setWatchlistItems(prev => prev.filter(w => w.id !== id));
    } catch (err) {
      toast.error('Failed to remove from watchlist');
    }
  }, []);

  const importHoldings = useCallback(async (newHoldings: Omit<HoldingLot, 'id' | 'createdAt'>[]) => {
    for (const h of newHoldings) {
      await api.createHolding({
        asset_id: h.assetId,
        quantity: h.quantity,
        avg_cost: h.avgCost,
        cost_currency: h.costCurrency,
        account_name: h.accountName,
        tags: h.tags || [],
        purchase_date: h.purchaseDate instanceof Date ? h.purchaseDate.toISOString() : h.purchaseDate,
      }).catch(() => toast.error('Failed to import holding'));
    }
    fetchData();
  }, [fetchData]);

  const searchAssets = useCallback((query: string): Asset[] => {
    if (!query) return [];
    const lowerQuery = query.toLowerCase();
    return assets.filter(a =>
      a.symbol.toLowerCase().includes(lowerQuery) ||
      a.name.toLowerCase().includes(lowerQuery)
    ).slice(0, 10);
  }, [assets]);

  // Auto-refresh at the user-configured interval (disabled when "Manual only" is selected)
  useEffect(() => {
    if (refreshIntervalMs === false) return;
    const interval = setInterval(fetchData, refreshIntervalMs);
    return () => clearInterval(interval);
  }, [fetchData, refreshIntervalMs]);

  return (
    <PortfolioContext.Provider value={{
      assets,
      holdings,
      watchlists,
      watchlistItems,
      activeWatchlistId,
      news,
      alerts,
      holdingsWithAssets: holdingsWithWeights,
      groupedHoldings,
      watchlistWithAssets,
      watchedAssetIds,
      portfolioSummary,
      topHoldings,
      topMovers,
      baseCurrency,
      setBaseCurrency,
      lastUpdated,
      isLoading,
      refreshPrices,
      addHolding,
      addCustomAsset,
      removeHolding,
      updateHolding,
      setActiveWatchlistId,
      createWatchlist,
      renameWatchlist,
      deleteWatchlist,
      addToWatchlist,
      updateWatchlistItem,
      removeFromWatchlist,
      importHoldings,
      searchAssets,
    }}>
      {children}
    </PortfolioContext.Provider>
  );
}

export function usePortfolio() {
  const context = useContext(PortfolioContext);
  if (context === undefined) {
    throw new Error('usePortfolio must be used within a PortfolioProvider');
  }
  return context;
}
