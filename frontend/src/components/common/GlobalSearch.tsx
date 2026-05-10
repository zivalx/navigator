import { useState, useRef, useEffect } from 'react';
import { Search, Star, Loader2, Check } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { api } from '@/lib/api';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

interface SearchResult {
  symbol: string;
  name: string;
  exchange: string;
  asset_type: string;
  currency: string;
  id?: string;
}

export function GlobalSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [pickerAsset, setPickerAsset] = useState<SearchResult | null>(null);
  const { addToWatchlist, watchlists, watchedAssetIds, assets } = usePortfolio();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const searchTimeout = useRef<ReturnType<typeof setTimeout>>();
  const inputRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcut: Cmd+K or Ctrl+K to focus
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === 'Escape') {
        if (pickerAsset) {
          setPickerAsset(null);
        } else {
          setIsOpen(false);
          inputRef.current?.blur();
        }
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [pickerAsset]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setPickerAsset(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const isWatched = (asset: SearchResult) => {
    if (asset.id && watchedAssetIds.has(asset.id)) return true;
    // Also check by symbol match against known assets
    const dbAsset = assets.find(a => a.symbol === asset.symbol);
    return dbAsset ? watchedAssetIds.has(dbAsset.id) : false;
  };

  const handleSearch = (value: string) => {
    setQuery(value);
    setSelectedIndex(-1);
    setPickerAsset(null);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);

    if (value.length < 1) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    setIsOpen(true);
    setIsLoading(true);

    // Show local matches immediately
    const local = assets
      .filter(a =>
        a.symbol.toLowerCase().includes(value.toLowerCase()) ||
        a.name.toLowerCase().includes(value.toLowerCase())
      )
      .slice(0, 3)
      .map(a => ({ ...a, asset_type: a.assetType }));
    setResults(local);

    // Debounced API search
    searchTimeout.current = setTimeout(async () => {
      try {
        const apiResults = await api.searchAssets(value);
        const localSymbols = new Set(local.map(r => r.symbol));
        const merged = [
          ...local,
          ...apiResults.filter((r: SearchResult) => !localSymbols.has(r.symbol)),
        ];
        setResults(merged.slice(0, 10));
      } catch {
        // Keep local results
      } finally {
        setIsLoading(false);
      }
    }, 250);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen || results.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => Math.min(prev + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && selectedIndex >= 0) {
      e.preventDefault();
      handleSelect(results[selectedIndex]);
    }
  };

  const ensureAssetInDb = async (asset: SearchResult): Promise<string> => {
    if (asset.id) return asset.id;
    // Check if already in local assets by symbol
    const local = assets.find(a => a.symbol === asset.symbol);
    if (local) return local.id;
    try {
      const existing = await api.getAssetBySymbol(asset.symbol);
      return existing.id;
    } catch {
      const created = await api.createAsset({
        symbol: asset.symbol,
        name: asset.name,
        exchange: asset.exchange || '',
        currency: asset.currency || 'USD',
        asset_type: asset.asset_type || 'stock',
        market_region: 'US',
        provider_ids: {},
      });
      return created.id;
    }
  };

  const handleSelect = (asset: SearchResult) => {
    navigate(`/markets?symbol=${asset.symbol}`);
    setQuery('');
    setResults([]);
    setIsOpen(false);
    setPickerAsset(null);
  };

  const handleStarClick = (e: React.MouseEvent, asset: SearchResult) => {
    e.stopPropagation();
    if (isWatched(asset)) return; // Already watched
    if (watchlists.length === 1) {
      // Only one watchlist — add directly
      handleAddToWatchlist(asset, watchlists[0].id);
    } else {
      // Multiple watchlists — show picker
      setPickerAsset(asset);
    }
  };

  const handleAddToWatchlist = async (asset: SearchResult, watchlistId: string) => {
    try {
      const assetId = await ensureAssetInDb(asset);
      addToWatchlist(assetId, watchlistId);
      const wlName = watchlists.find(w => w.id === watchlistId)?.name || 'watchlist';
      toast.success(`Added ${asset.symbol} to ${wlName}`);
    } catch {
      toast.error('Failed to add to watchlist');
    }
    setPickerAsset(null);
  };

  const typeLabel = (type: string) => {
    const labels: Record<string, string> = {
      stock: 'Stock', etf: 'ETF', crypto: 'Crypto',
      fund: 'Fund', index: 'Index',
    };
    return labels[type] || type;
  };

  const typeColor = (type: string) => {
    const colors: Record<string, string> = {
      stock: 'text-blue-400', etf: 'text-emerald-400', crypto: 'text-amber-400',
      fund: 'text-purple-400', index: 'text-cyan-400',
    };
    return colors[type] || 'text-muted-foreground';
  };

  return (
    <div ref={containerRef} className="relative hidden md:block">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
      <Input
        ref={inputRef}
        type="text"
        placeholder="Search assets... (⌘K)"
        value={query}
        onChange={(e) => handleSearch(e.target.value)}
        onFocus={() => query.length > 0 && setIsOpen(true)}
        onKeyDown={handleKeyDown}
        className="w-72 pl-9 bg-muted/50 border-border"
      />
      {isLoading && (
        <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground animate-spin" />
      )}

      {isOpen && results.length > 0 && (
        <div className="absolute top-full mt-1 w-[420px] right-0 bg-popover border border-border rounded-lg shadow-xl overflow-hidden z-50">
          <div className="max-h-[400px] overflow-y-auto">
            {results.map((asset, idx) => {
              const watched = isWatched(asset);
              return (
                <div
                  key={`${asset.symbol}-${idx}`}
                  className={cn(
                    'flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-border last:border-0 transition-colors',
                    idx === selectedIndex ? 'bg-muted' : 'hover:bg-muted/50'
                  )}
                  onClick={() => handleSelect(asset)}
                  onMouseEnter={() => setSelectedIndex(idx)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{asset.symbol}</span>
                      <span className={cn('text-xs font-medium', typeColor(asset.asset_type))}>
                        {typeLabel(asset.asset_type)}
                      </span>
                      {asset.exchange && (
                        <span className="text-xs text-muted-foreground">{asset.exchange}</span>
                      )}
                    </div>
                    <span className="text-sm text-muted-foreground truncate block">
                      {asset.name}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={(e) => handleStarClick(e, asset)}
                      className={cn(
                        'p-1.5 rounded-md transition-colors',
                        watched
                          ? 'text-yellow-400 cursor-default'
                          : 'text-muted-foreground hover:bg-primary/10 hover:text-primary'
                      )}
                      title={watched ? 'Already in watchlist' : 'Add to watchlist'}
                    >
                      <Star className={cn('h-4 w-4', watched && 'fill-current')} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Watchlist picker popover */}
          {pickerAsset && (
            <div className="border-t border-border bg-muted/50 px-4 py-3">
              <span className="text-xs font-medium text-muted-foreground mb-2 block">
                Add {pickerAsset.symbol} to:
              </span>
              <div className="flex flex-wrap gap-1.5">
                {watchlists.map(wl => (
                  <button
                    key={wl.id}
                    onClick={(e) => { e.stopPropagation(); handleAddToWatchlist(pickerAsset, wl.id); }}
                    className="px-3 py-1.5 text-sm rounded-md border border-border bg-background hover:bg-primary hover:text-primary-foreground transition-colors"
                  >
                    {wl.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="px-4 py-2 bg-muted/30 border-t border-border">
            <span className="text-xs text-muted-foreground">
              ↑↓ navigate · Enter to view · ⭐ add to watchlist
            </span>
          </div>
        </div>
      )}

      {isOpen && query.length > 0 && results.length === 0 && !isLoading && (
        <div className="absolute top-full mt-1 w-[420px] right-0 bg-popover border border-border rounded-lg shadow-xl z-50 p-6 text-center">
          <span className="text-sm text-muted-foreground">No results for "{query}"</span>
        </div>
      )}
    </div>
  );
}
