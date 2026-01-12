import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import { 
  Asset, HoldingLot, WatchlistItem, Watchlist, NewsItem, Currency, 
  HoldingWithAsset, WatchlistItemWithAsset, PortfolioSummary, Alert, MarketMover, GroupedHolding 
} from '@/lib/types';
import { 
  mockAssets, mockHoldings, mockWatchlists, mockWatchlistItems, mockNews, mockPrices, 
  getAssetById, getPriceForAsset, convertCurrency 
} from '@/lib/mockData';

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
  const [assets, setAssets] = useState<Asset[]>(mockAssets);
  const [customPrices, setCustomPrices] = useState<Record<string, number>>({});
  const [holdings, setHoldings] = useState<HoldingLot[]>(mockHoldings);
  const [watchlists, setWatchlists] = useState<Watchlist[]>(mockWatchlists);
  const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>(mockWatchlistItems);
  const [activeWatchlistId, setActiveWatchlistId] = useState<string | null>(mockWatchlists[0]?.id || null);
  const [news] = useState<NewsItem[]>(mockNews);
  const [baseCurrency, setBaseCurrency] = useState<Currency>('USD');
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const isDemoMode = true;

  // Compute holdings with current prices
  const holdingsWithAssets: HoldingWithAsset[] = holdings.map(holding => {
    const asset = getAssetById(holding.assetId) || assets.find(a => a.id === holding.assetId);
    const priceData = getPriceForAsset(holding.assetId);
    // Check for custom price (for "other" assets)
    const customPrice = customPrices[holding.assetId];
    
    if (!asset) {
      return { ...holding, asset: { id: holding.assetId, symbol: 'UNKNOWN', name: 'Unknown Asset', currency: 'USD' as Currency, assetType: 'stock' as const, marketRegion: 'US' as const } };
    }
    
    const currentPrice = customPrice ?? priceData?.price;
    const priceChange = priceData?.change;
    const priceChangePercent = priceData?.changePercent;
    
    let marketValue: number | undefined;
    let unrealizedPnL: number | undefined;
    let unrealizedPnLPercent: number | undefined;
    
    if (currentPrice !== undefined) {
      const priceInBase = convertCurrency(currentPrice, asset.currency, baseCurrency);
      const costInBase = convertCurrency(holding.avgCost, holding.costCurrency, baseCurrency);
      
      marketValue = holding.quantity * priceInBase;
      unrealizedPnL = marketValue - (holding.quantity * costInBase);
      unrealizedPnLPercent = ((priceInBase - costInBase) / costInBase) * 100;
    }
    
    return {
      ...holding,
      asset,
      currentPrice,
      priceChange,
      priceChangePercent,
      marketValue,
      unrealizedPnL,
      unrealizedPnLPercent,
    };
  });

  // Calculate total NAV for weights
  const totalNav = holdingsWithAssets.reduce((sum, h) => sum + (h.marketValue || 0), 0);
  
  // Add weights
  const holdingsWithWeights = holdingsWithAssets.map(h => ({
    ...h,
    weight: totalNav > 0 && h.marketValue ? (h.marketValue / totalNav) * 100 : 0,
  }));

  // Portfolio summary
  const portfolioSummary: PortfolioSummary = {
    totalNav,
    baseCurrency,
    dailyPnL: holdingsWithAssets.reduce((sum, h) => {
      if (h.currentPrice && h.priceChange) {
        const changeInBase = convertCurrency(h.priceChange, h.asset.currency, baseCurrency);
        return sum + (h.quantity * changeInBase);
      }
      return sum;
    }, 0),
    dailyPnLPercent: 0,
    totalCost: holdingsWithAssets.reduce((sum, h) => {
      const costInBase = convertCurrency(h.avgCost, h.costCurrency, baseCurrency);
      return sum + (h.quantity * costInBase);
    }, 0),
    totalUnrealizedPnL: holdingsWithAssets.reduce((sum, h) => sum + (h.unrealizedPnL || 0), 0),
    totalUnrealizedPnLPercent: 0,
    lastUpdated,
  };
  
  portfolioSummary.dailyPnLPercent = portfolioSummary.totalNav > 0 
    ? (portfolioSummary.dailyPnL / (portfolioSummary.totalNav - portfolioSummary.dailyPnL)) * 100 
    : 0;
  portfolioSummary.totalUnrealizedPnLPercent = portfolioSummary.totalCost > 0
    ? (portfolioSummary.totalUnrealizedPnL / portfolioSummary.totalCost) * 100
    : 0;

  // Top holdings by weight
  const topHoldings = [...holdingsWithWeights]
    .sort((a, b) => (b.weight || 0) - (a.weight || 0))
    .slice(0, 5);

  // Group holdings by asset for expandable view
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
  const holdingsWithPriceChange = holdingsWithAssets.filter(h => h.priceChangePercent !== undefined);
  const sortedByChange = [...holdingsWithPriceChange].sort((a, b) => (b.priceChangePercent || 0) - (a.priceChangePercent || 0));
  
  const topMovers = {
    gainers: sortedByChange.filter(h => (h.priceChangePercent || 0) > 0).slice(0, 3).map(h => ({
      asset: h.asset,
      price: h.currentPrice || 0,
      change: h.priceChange || 0,
      changePercent: h.priceChangePercent || 0,
    })),
    losers: sortedByChange.filter(h => (h.priceChangePercent || 0) < 0).slice(-3).reverse().map(h => ({
      asset: h.asset,
      price: h.currentPrice || 0,
      change: h.priceChange || 0,
      changePercent: h.priceChangePercent || 0,
    })),
  };

  // Watchlist items for active watchlist with assets
  const activeWatchlistItems = activeWatchlistId 
    ? watchlistItems.filter(item => item.watchlistId === activeWatchlistId)
    : [];
    
  const watchlistWithAssets: WatchlistItemWithAsset[] = activeWatchlistItems.map(item => {
    const asset = getAssetById(item.assetId);
    const priceData = getPriceForAsset(item.assetId);
    
    if (!asset) {
      return { 
        ...item, 
        asset: { id: item.assetId, symbol: 'UNKNOWN', name: 'Unknown Asset', currency: 'USD' as Currency, assetType: 'stock' as const, marketRegion: 'US' as const } 
      };
    }
    
    return {
      ...item,
      asset,
      currentPrice: priceData?.price,
      priceChange: priceData?.change,
      priceChangePercent: priceData?.changePercent,
    };
  });

  // Actions
  const refreshPrices = useCallback(() => {
    setLastUpdated(new Date());
  }, []);

  const addHolding = useCallback((holding: Omit<HoldingLot, 'id' | 'createdAt'>) => {
    const newHolding: HoldingLot = {
      ...holding,
      id: `h${Date.now()}`,
      createdAt: new Date(),
    };
    setHoldings(prev => [...prev, newHolding]);
  }, []);

  const removeHolding = useCallback((id: string) => {
    setHoldings(prev => prev.filter(h => h.id !== id));
  }, []);

  const updateHolding = useCallback((id: string, updates: Partial<HoldingLot>) => {
    setHoldings(prev => prev.map(h => h.id === id ? { ...h, ...updates } : h));
  }, []);

  const addCustomAsset = useCallback((
    assetData: Omit<Asset, 'id'>, 
    holdingData: { quantity: number; avgCost: number; currentPrice: number; accountName: string; purchaseDate: Date }
  ) => {
    const assetId = `custom_${Date.now()}`;
    const newAsset: Asset = { ...assetData, id: assetId };
    
    setAssets(prev => [...prev, newAsset]);
    setCustomPrices(prev => ({ ...prev, [assetId]: holdingData.currentPrice }));
    
    const newHolding: HoldingLot = {
      id: `h${Date.now()}`,
      assetId,
      quantity: holdingData.quantity,
      avgCost: holdingData.avgCost,
      costCurrency: assetData.currency,
      accountName: holdingData.accountName,
      tags: [],
      purchaseDate: holdingData.purchaseDate,
      createdAt: new Date(),
    };
    setHoldings(prev => [...prev, newHolding]);
  }, []);

  // Watchlist actions
  const createWatchlist = useCallback((name: string) => {
    const newWatchlist: Watchlist = {
      id: `wl${Date.now()}`,
      name,
      createdAt: new Date(),
    };
    setWatchlists(prev => [...prev, newWatchlist]);
    setActiveWatchlistId(newWatchlist.id);
  }, []);

  const renameWatchlist = useCallback((id: string, name: string) => {
    setWatchlists(prev => prev.map(wl => wl.id === id ? { ...wl, name } : wl));
  }, []);

  const deleteWatchlist = useCallback((id: string) => {
    setWatchlists(prev => prev.filter(wl => wl.id !== id));
    setWatchlistItems(prev => prev.filter(item => item.watchlistId !== id));
    setActiveWatchlistId(prev => {
      if (prev === id) {
        const remaining = watchlists.filter(wl => wl.id !== id);
        return remaining[0]?.id || null;
      }
      return prev;
    });
  }, [watchlists]);

  const addToWatchlist = useCallback((assetId: string, watchlistId?: string, notes?: string, targetPrice?: number) => {
    const targetWatchlistId = watchlistId || activeWatchlistId;
    if (!targetWatchlistId) return;
    
    // Check if already in this watchlist
    if (watchlistItems.some(w => w.assetId === assetId && w.watchlistId === targetWatchlistId)) return;
    
    const newItem: WatchlistItem = {
      id: `w${Date.now()}`,
      watchlistId: targetWatchlistId,
      assetId,
      notes,
      targetPrice,
      createdAt: new Date(),
    };
    setWatchlistItems(prev => [...prev, newItem]);
  }, [activeWatchlistId, watchlistItems]);

  const updateWatchlistItem = useCallback((id: string, updates: { notes?: string; targetPrice?: number }) => {
    setWatchlistItems(prev => prev.map(w => w.id === id ? { ...w, ...updates } : w));
  }, []);

  const removeFromWatchlist = useCallback((id: string) => {
    setWatchlistItems(prev => prev.filter(w => w.id !== id));
  }, []);

  const importHoldings = useCallback((newHoldings: Omit<HoldingLot, 'id' | 'createdAt'>[]) => {
    const holdingsWithIds = newHoldings.map((h, i) => ({
      ...h,
      id: `h${Date.now()}_${i}`,
      createdAt: new Date(),
    }));
    setHoldings(prev => [...prev, ...holdingsWithIds]);
  }, []);

  const searchAssets = useCallback((query: string): Asset[] => {
    if (!query) return [];
    const lowerQuery = query.toLowerCase();
    return assets.filter(a => 
      a.symbol.toLowerCase().includes(lowerQuery) || 
      a.name.toLowerCase().includes(lowerQuery)
    ).slice(0, 10);
  }, [assets]);

  // Auto-refresh
  useEffect(() => {
    const interval = setInterval(refreshPrices, 60000);
    return () => clearInterval(interval);
  }, [refreshPrices]);

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
