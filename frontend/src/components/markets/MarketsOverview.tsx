import { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Search, TrendingUp, TrendingDown, Settings, RotateCcw } from 'lucide-react';
import { PercentChange } from '@/components/common/PriceDisplay';
import { cn } from '@/lib/utils';
import { MarketAssetCard } from './MarketAssetCard';
import { AddMarketCardDialog } from './AddMarketCardDialog';
import { useMarketCards } from '@/contexts/MarketCardsContext';
import { defaultMarketCards } from '@/lib/marketCardTypes';
import { toast } from 'sonner';

type Region = 'ALL' | 'US' | 'EU' | 'ASIA';

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
          </h3>
          <div className="space-y-2">
            {[
              { symbol: 'NVDA', name: 'NVIDIA', price: 138.72, change: 4.26 },
              { symbol: 'AMD', name: 'AMD Inc.', price: 142.50, change: 3.82 },
              { symbol: 'SMCI', name: 'Super Micro', price: 589.20, change: 3.15 },
              { symbol: 'ARM', name: 'ARM Holdings', price: 168.45, change: 2.98 },
              { symbol: 'MU', name: 'Micron', price: 98.32, change: 2.67 },
            ].map((stock) => (
              <div 
                key={stock.symbol}
                className="flex items-center justify-between p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors cursor-pointer"
              >
                <div>
                  <span className="font-semibold">{stock.symbol}</span>
                  <span className="text-sm text-muted-foreground ml-2">{stock.name}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono">${stock.price.toFixed(2)}</span>
                  <PercentChange value={stock.change} size="sm" className="ml-2" />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Top Losers */}
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingDown className="h-5 w-5 text-destructive" />
            Top Losers
          </h3>
          <div className="space-y-2">
            {[
              { symbol: 'BA', name: 'Boeing', price: 178.32, change: -4.52 },
              { symbol: 'DIS', name: 'Disney', price: 112.45, change: -3.21 },
              { symbol: 'NFLX', name: 'Netflix', price: 485.20, change: -2.87 },
              { symbol: 'PFE', name: 'Pfizer', price: 28.45, change: -2.45 },
              { symbol: 'WBA', name: 'Walgreens', price: 12.34, change: -2.12 },
            ].map((stock) => (
              <div 
                key={stock.symbol}
                className="flex items-center justify-between p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors cursor-pointer"
              >
                <div>
                  <span className="font-semibold">{stock.symbol}</span>
                  <span className="text-sm text-muted-foreground ml-2">{stock.name}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono">${stock.price.toFixed(2)}</span>
                  <PercentChange value={stock.change} size="sm" className="ml-2" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
