import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Search, TrendingUp, TrendingDown, Settings, RotateCcw } from 'lucide-react';
import { PercentChange } from '@/components/common/PriceDisplay';
import { cn, formatMoney } from '@/lib/utils';
import type { MarketRegion } from '@/lib/types';
import { MarketAssetCard } from './MarketAssetCard';
import { AddMarketCardDialog } from './AddMarketCardDialog';
import { useMarketCards } from '@/contexts/MarketCardsContext';
import { defaultMarketCards } from '@/lib/marketCardTypes';
import { api } from '@/lib/api';
import { useAppSettings } from '@/contexts/AppSettingsContext';

type Region = 'ALL' | MarketRegion;

export function MarketsOverview() {
  const { cards, isEditMode, setEditMode } = useMarketCards();
  const [activeRegion, setActiveRegion] = useState<Region>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredCards = cards.filter(card => {
    const matchesRegion = activeRegion === 'ALL' || card.region === activeRegion;
    const matchesSearch = !searchQuery ||
      card.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
      card.name.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesRegion && matchesSearch;
  });

  const { refreshIntervalMs } = useAppSettings();
  const isIsrael = activeRegion === 'IL';

  // Israel: the backend scans Tel Aviv large caps (prices in ILS major units).
  const { data: ilMovers, isLoading: ilMoversLoading } = useQuery({
    queryKey: ['market-movers', 'IL'],
    queryFn: async () => {
      const [g, l] = await Promise.all([api.getTopGainers(5, 'IL'), api.getTopLosers(5, 'IL')]);
      return { gainers: g, losers: l };
    },
    enabled: isIsrael,
    refetchInterval: refreshIntervalMs,
  });

  // Other tabs: real gainers/losers from the stock cards shown in that tab.
  const cardMovers = useMemo(() => {
    const withChange = cards
      .filter(c => c.assetType !== 'index')
      .filter(c => activeRegion === 'ALL' || c.region === activeRegion)
      .filter(c => c.changePercent !== undefined && c.price !== undefined)
      .map(c => ({ symbol: c.symbol, name: c.name, price: c.price!, currency: c.currency || 'USD', change: c.changePercent! }));
    const sorted = [...withChange].sort((a, b) => b.change - a.change);
    return {
      gainers: sorted.filter(s => s.change > 0).slice(0, 5),
      losers: sorted.filter(s => s.change < 0).reverse().slice(0, 5),
    };
  }, [cards, activeRegion]);

  const { gainers, losers } = useMemo(() => {
    if (!isIsrael) return cardMovers;
    type Quote = { symbol: string; name?: string; price: number; currency?: string; changePercent?: number };
    const toRow = (q: Quote) => ({
      symbol: q.symbol, name: q.name ?? '', price: q.price, currency: q.currency || 'ILS', change: q.changePercent ?? 0,
    });
    return {
      gainers: ((ilMovers?.gainers ?? []) as Quote[]).map(toRow),
      losers: ((ilMovers?.losers ?? []) as Quote[]).map(toRow),
    };
  }, [isIsrael, cardMovers, ilMovers]);
  const moversEmpty = (kind: string) =>
    isIsrael && ilMoversLoading ? 'Loading Tel Aviv movers…' : `No ${kind} today`;

  const handleResetCards = () => {
    localStorage.setItem('marketCards', JSON.stringify(defaultMarketCards));
    window.location.reload();
  };

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
        <div className="flex items-center gap-2">
          <Tabs value={activeRegion} onValueChange={(v) => setActiveRegion(v as Region)}>
            <TabsList className="bg-muted/50">
              <TabsTrigger value="ALL">All</TabsTrigger>
              <TabsTrigger value="US" className="gap-1">
                <span>🇺🇸</span> US
              </TabsTrigger>
              <TabsTrigger value="EU" className="gap-1">
                <span>🇪🇺</span> EU
              </TabsTrigger>
              <TabsTrigger value="ASIA" className="gap-1">
                <span>🌏</span> Asia
              </TabsTrigger>
              <TabsTrigger value="IL" className="gap-1">
                <span>🇮🇱</span> Israel
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search markets..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          
          <AddMarketCardDialog />
          
          <Button
            variant={isEditMode ? 'default' : 'outline'}
            size="icon"
            onClick={() => setEditMode(!isEditMode)}
            title={isEditMode ? 'Done editing' : 'Edit layout'}
          >
            <Settings className={cn('h-4 w-4', isEditMode && 'animate-spin-slow')} />
          </Button>
        </div>
      </div>

      {/* Edit Mode Banner */}
      {isEditMode && (
        <div className="bg-primary/10 border border-primary/20 rounded-lg p-3 flex items-center justify-between">
          <span className="text-sm">
            <strong>Edit Mode:</strong> Use arrows to reorder cards, or remove cards you don't need.
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleResetCards} className="gap-1">
              <RotateCcw className="h-3 w-3" />
              Reset
            </Button>
            <Button size="sm" onClick={() => setEditMode(false)}>
              Done
            </Button>
          </div>
        </div>
      )}

      {/* Market Cards Grid */}
      {filteredCards.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredCards.map((card, index) => (
            <MarketAssetCard
              key={card.id}
              card={card}
              isFirst={index === 0}
              isLast={index === filteredCards.length - 1}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 border border-dashed rounded-xl">
          <p className="text-muted-foreground mb-4">No market cards yet</p>
          <AddMarketCardDialog />
        </div>
      )}

      {/* Market Movers Section */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-8">
        {/* Top Gainers */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-success" />
            Top Gainers
            {isIsrael && <span className="text-xs font-normal text-muted-foreground">Tel Aviv large caps</span>}
          </h3>
          <div className="space-y-2">
            {gainers.length > 0 ? gainers.map((stock) => (
              <div
                key={stock.symbol}
                className="flex items-center justify-between p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors cursor-pointer"
              >
                <div>
                  <span className="font-semibold">{stock.symbol}</span>
                  <span className="text-sm text-muted-foreground ml-2">{stock.name}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono">{formatMoney(stock.price, stock.currency)}</span>
                  <PercentChange value={stock.change} size="sm" className="ml-2" />
                </div>
              </div>
            )) : (
              <p className="text-sm text-muted-foreground py-4 text-center">{moversEmpty('gainers')}</p>
            )}
          </div>
        </div>

        {/* Top Losers */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingDown className="h-5 w-5 text-destructive" />
            Top Losers
            {isIsrael && <span className="text-xs font-normal text-muted-foreground">Tel Aviv large caps</span>}
          </h3>
          <div className="space-y-2">
            {losers.length > 0 ? losers.map((stock) => (
              <div
                key={stock.symbol}
                className="flex items-center justify-between p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors cursor-pointer"
              >
                <div>
                  <span className="font-semibold">{stock.symbol}</span>
                  <span className="text-sm text-muted-foreground ml-2">{stock.name}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono">{formatMoney(stock.price, stock.currency)}</span>
                  <PercentChange value={stock.change} size="sm" className="ml-2" />
                </div>
              </div>
            )) : (
              <p className="text-sm text-muted-foreground py-4 text-center">{moversEmpty('losers')}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
