import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import {
  Asset, HoldingLot, WatchlistItem, Watchlist, NewsItem, Currency,
  HoldingWithAsset, WatchlistItemWithAsset, PortfolioSummary, Alert, MarketMover, GroupedHolding
} from '@/lib/types';
import { api } from '@/lib/api';
import { toast } from 'sonner';

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
  portfolioSummary: PortfolioSummary;
  topHoldings: HoldingWithAsset[];
  topMovers: { gainers: MarketMover[]; losers: MarketMover[] };

  // Settings
  baseCurrency: Currency;
  setBaseCurrency: (currency: Currency) => void;
  isDemoMode: boolean;
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
  const [activeWatchlistId, setActiveWatchlistId] = useState<string | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [baseCurrency, setBaseCurrency] = useState<Currency>('USD');
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const isDemoMode = false;

  // Fetch all data from API
  const fetchData = useCallback(async () => {
    try {
      setIsLoading(true);
      const [assetsData, holdingsData, watchlistsData] = await Promise.all([
        api.getAssets(),
        api.getHoldings().catch(() => []),
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
  }, []);

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

  // Portfolio summary
  const portfolioSummary: PortfolioSummary = useMemo(() => {
    const dailyPnL = holdingsWithWeights.reduce((sum, h) => {
      if (h.currentPrice && h.priceChange) {
        return sum + (h.quantity * h.priceChange);
      }
      return sum;
    }, 0);
    const totalCost = holdingsWithWeights.reduce((sum, h) => sum + (h.quantity * h.avgCost), 0);
    const totalUnrealizedPnL = holdingsWithWeights.reduce((sum, h) => sum + (h.unrealizedPnL || 0), 0);

    return {
      totalNav,
      baseCurrency,
      dailyPnL,
      dailyPnLPercent: totalNav > 0 ? (dailyPnL / (totalNav - dailyPnL)) * 100 : 0,
      totalCost,
      totalUnrealizedPnL,
      totalUnrealizedPnLPercent: totalCost > 0 ? (totalUnrealizedPnL / totalCost) * 100 : 0,
      lastUpdated,
    };
  }, [holdingsWithWeights, totalNav, baseCurrency, lastUpdated]);

  // Top holdings by weight
  const topHoldings = useMemo(() =>
    [...holdingsWithWeights].sort((a, b) => (b.weight || 0) - (a.weight || 0)).slice(0, 5),
    [holdingsWithWeights]
  );

  // Grouped holdings
  const groupedHoldings: GroupedHolding[] = useMemo(() => {
    const groups = new Map<string, HoldingWithAsset[]>();
    holdingsWithWeights.forEach(h => {
      const existing = groups.get(h.assetId) || [];
      groups.set(h.assetId, [...existing, h]);
    });

    return Array.from(groups.entries()).map(([assetId, lots]) => {
      const asset = lots[0].asset;
      const totalQuantity = lots.reduce((sum, l) => sum + l.quantity, 0);
      const totalCost = lots.reduce((sum, l) => sum + (l.quantity * l.avgCost), 0);
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

  // Top movers
  const topMovers = useMemo(() => {
    const withChange = holdingsWithWeights.filter(h => h.priceChangePercent !== undefined);
    const sorted = [...withChange].sort((a, b) => (b.priceChangePercent || 0) - (a.priceChangePercent || 0));
    return {
      gainers: sorted.filter(h => (h.priceChangePercent || 0) > 0).slice(0, 3).map(h => ({
        asset: h.asset, price: h.currentPrice || 0, change: h.priceChange || 0, changePercent: h.priceChangePercent || 0,
      })),
      losers: sorted.filter(h => (h.priceChangePercent || 0) < 0).slice(-3).reverse().map(h => ({
        asset: h.asset, price: h.currentPrice || 0, change: h.priceChange || 0, changePercent: h.priceChangePercent || 0,
      })),
    };
  }, [holdingsWithWeights]);

  // Watchlist items with assets
  const watchlistWithAssets: WatchlistItemWithAsset[] = useMemo(() => {
    return watchlistItems.map(item => {
      const asset = assets.find(a => a.id === item.assetId);
      return {
        ...item,
        asset: asset || { id: item.assetId, symbol: 'UNKNOWN', name: 'Unknown', currency: 'USD' as Currency, assetType: 'stock' as const, marketRegion: 'US' as const },
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

  // Auto-refresh every 60 seconds
  useEffect(() => {
    const interval = setInterval(fetchData, 60000);
    return () => clearInterval(interval);
  }, [fetchData]);

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
      portfolioSummary,
      topHoldings,
      topMovers,
      baseCurrency,
      setBaseCurrency,
      isDemoMode,
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
