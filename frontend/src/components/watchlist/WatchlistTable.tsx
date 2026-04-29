import { useState, useRef } from 'react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { api } from '@/lib/api';
import { PercentChange } from '@/components/common/PriceDisplay';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Plus, Search, Trash2, Target, MessageSquare, MoreHorizontal, Star, Edit2, FolderPlus } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { EmptyState } from '@/components/common/EmptyState';
import { toast } from 'sonner';

export function WatchlistTable() {
  const { 
    watchlistWithAssets, 
    watchlists,
    activeWatchlistId,
    setActiveWatchlistId,
    createWatchlist,
    renameWatchlist,
    deleteWatchlist,
    removeFromWatchlist,
    updateWatchlistItem,
    addHolding, 
    searchAssets, 
    addToWatchlist 
  } = usePortfolio();
  
  const [searchQuery, setSearchQuery] = useState('');
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [symbolSearch, setSymbolSearch] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  
  // Watchlist management dialogs
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [newWatchlistName, setNewWatchlistName] = useState('');
  const [watchlistToEdit, setWatchlistToEdit] = useState<string | null>(null);
  
  // Edit item dialog
  const [editItemDialogOpen, setEditItemDialogOpen] = useState(false);
  const [itemToEdit, setItemToEdit] = useState<{ id: string; symbol: string; notes: string; targetPrice: string } | null>(null);

  const activeWatchlist = watchlists.find(wl => wl.id === activeWatchlistId);

  const searchTimeout = useRef<ReturnType<typeof setTimeout>>();

  const handleSearch = (query: string) => {
    setSymbolSearch(query);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);

    if (query.length < 1) {
      setSearchResults([]);
      return;
    }

    // Show local matches immediately
    setSearchResults(searchAssets(query));

    // Then search via API for assets not in DB (debounced)
    searchTimeout.current = setTimeout(async () => {
      try {
        const apiResults = await api.searchAssets(query);
        setSearchResults(apiResults);
      } catch {
        // Keep showing local results on API failure
      }
    }, 300);
  };

  const handleAddToWatchlist = async (asset: any) => {
    try {
      let assetId = asset.id;

      // If the asset isn't in our DB yet, find or create it
      if (!assetId) {
        // Try to find by symbol first
        try {
          const existing = await api.getAssetBySymbol(asset.symbol);
          assetId = existing.id;
        } catch {
          // Not found — create it
          const created = await api.createAsset({
            symbol: asset.symbol,
            name: asset.name,
            exchange: asset.exchange || '',
            currency: asset.currency || 'USD',
            asset_type: asset.assetType || asset.asset_type || 'stock',
            market_region: asset.marketRegion || asset.market_region || 'US',
            provider_ids: {},
          });
          assetId = created.id;
        }
      }

      addToWatchlist(assetId);
      toast.success(`Added ${asset.symbol} to ${activeWatchlist?.name || 'watchlist'}`);
    } catch (err: any) {
      toast.error(err?.message || 'Failed to add symbol');
    }
    setAddDialogOpen(false);
    setSymbolSearch('');
    setSearchResults([]);
  };

  const handleConvertToHolding = (item: any) => {
    addHolding({
      assetId: item.assetId,
      quantity: 1,
      avgCost: item.currentPrice ?? 100,
      costCurrency: item.asset.currency,
      accountName: 'Main',
      tags: [],
      purchaseDate: new Date(),
    });
    removeFromWatchlist(item.id);
    toast.success(`Converted ${item.asset.symbol} to holding`);
  };

  const handleCreateWatchlist = () => {
    if (!newWatchlistName.trim()) return;
    createWatchlist(newWatchlistName.trim());
    toast.success(`Created watchlist "${newWatchlistName.trim()}"`);
    setNewWatchlistName('');
    setCreateDialogOpen(false);
  };

  const handleRenameWatchlist = () => {
    if (!newWatchlistName.trim() || !watchlistToEdit) return;
    renameWatchlist(watchlistToEdit, newWatchlistName.trim());
    toast.success('Watchlist renamed');
    setNewWatchlistName('');
    setWatchlistToEdit(null);
    setRenameDialogOpen(false);
  };

  const handleDeleteWatchlist = () => {
    if (!watchlistToEdit) return;
    const watchlistName = watchlists.find(wl => wl.id === watchlistToEdit)?.name;
    deleteWatchlist(watchlistToEdit);
    toast.success(`Deleted "${watchlistName}"`);
    setWatchlistToEdit(null);
    setDeleteDialogOpen(false);
  };

  const openRenameDialog = (id: string) => {
    const wl = watchlists.find(w => w.id === id);
    if (wl) {
      setWatchlistToEdit(id);
      setNewWatchlistName(wl.name);
      setRenameDialogOpen(true);
    }
  };

  const openDeleteDialog = (id: string) => {
    setWatchlistToEdit(id);
    setDeleteDialogOpen(true);
  };

  const openEditItemDialog = (item: any) => {
    setItemToEdit({
      id: item.id,
      symbol: item.asset.symbol,
      notes: item.notes || '',
      targetPrice: item.targetPrice?.toString() || '',
    });
    setEditItemDialogOpen(true);
  };

  const handleUpdateItem = () => {
    if (!itemToEdit) return;
    const targetPrice = itemToEdit.targetPrice ? parseFloat(itemToEdit.targetPrice) : undefined;
    updateWatchlistItem(itemToEdit.id, {
      notes: itemToEdit.notes || undefined,
      targetPrice: isNaN(targetPrice as number) ? undefined : targetPrice,
    });
    toast.success(`Updated ${itemToEdit.symbol}`);
    setItemToEdit(null);
    setEditItemDialogOpen(false);
  };

  const filteredWatchlist = searchQuery
    ? watchlistWithAssets.filter(item =>
        item.asset.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.asset.name.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : watchlistWithAssets;

  // No watchlists at all
  if (watchlists.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-8">
        <EmptyState
          icon={<Star className="h-8 w-8" />}
          title="No watchlists yet"
          description="Create a watchlist to start tracking assets."
          action={
            <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
              <DialogTrigger asChild>
                <Button className="gap-2">
                  <FolderPlus className="h-4 w-4" />
                  Create Watchlist
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Create Watchlist</DialogTitle>
                  <DialogDescription>
                    Give your watchlist a name to get started.
                  </DialogDescription>
                </DialogHeader>
                <div className="py-4">
                  <Input
                    placeholder="Watchlist name..."
                    value={newWatchlistName}
                    onChange={(e) => setNewWatchlistName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreateWatchlist()}
                  />
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
                  <Button onClick={handleCreateWatchlist} disabled={!newWatchlistName.trim()}>Create</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Watchlist Tabs */}
      <div className="flex items-center gap-3">
        <Tabs value={activeWatchlistId || ''} onValueChange={setActiveWatchlistId} className="flex-1">
          <TabsList className="h-auto p-1 bg-muted/50">
            {watchlists.map(wl => (
              <TabsTrigger 
                key={wl.id} 
                value={wl.id}
                className="gap-2 data-[state=active]:bg-background"
              >
                {wl.name}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                    <Button variant="ghost" size="icon" className="h-5 w-5 ml-1 hover:bg-muted">
                      <MoreHorizontal className="h-3 w-3" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="bg-popover">
                    <DropdownMenuItem onClick={() => openRenameDialog(wl.id)}>
                      <Edit2 className="h-4 w-4 mr-2" /> Rename
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem 
                      className="text-destructive"
                      onClick={() => openDeleteDialog(wl.id)}
                      disabled={watchlists.length === 1}
                    >
                      <Trash2 className="h-4 w-4 mr-2" /> Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        
        <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
          <DialogTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              <FolderPlus className="h-4 w-4" />
              New List
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Watchlist</DialogTitle>
              <DialogDescription>
                Give your watchlist a name to get started.
              </DialogDescription>
            </DialogHeader>
            <div className="py-4">
              <Input
                placeholder="Watchlist name..."
                value={newWatchlistName}
                onChange={(e) => setNewWatchlistName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateWatchlist()}
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
              <Button onClick={handleCreateWatchlist} disabled={!newWatchlistName.trim()}>Create</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* Header */}
      <div className="flex flex-col sm:flex-row gap-3 justify-between">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search watchlist..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
          <DialogTrigger asChild>
            <Button className="gap-2">
              <Plus className="h-4 w-4" />
              Add Symbol
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add to {activeWatchlist?.name || 'Watchlist'}</DialogTitle>
              <DialogDescription>
                Search for an asset to add to your watchlist.
              </DialogDescription>
            </DialogHeader>
            <div className="py-4">
              <Input
                placeholder="Search symbols..."
                value={symbolSearch}
                onChange={(e) => handleSearch(e.target.value)}
              />
              {searchResults.length > 0 && (
                <div className="mt-3 border border-border rounded-lg overflow-hidden max-h-64 overflow-y-auto bg-popover">
                  {searchResults.map((asset, idx) => (
                    <button
                      key={asset.id || `${asset.symbol}-${idx}`}
                      className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/50 transition-colors border-b border-border last:border-0"
                      onClick={() => handleAddToWatchlist(asset)}
                    >
                      <div className="flex items-center gap-3">
                        <span className="font-semibold">{asset.symbol}</span>
                        <span className="text-sm text-muted-foreground">{asset.name}</span>
                      </div>
                      <Plus className="h-4 w-4 text-primary" />
                    </button>
                  ))}
                </div>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {/* Empty state for active watchlist */}
      {watchlistWithAssets.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-8">
          <EmptyState
            icon={<Star className="h-8 w-8" />}
            title={`${activeWatchlist?.name || 'Watchlist'} is empty`}
            description="Add symbols to track their performance."
            action={
              <Button className="gap-2" onClick={() => setAddDialogOpen(true)}>
                <Plus className="h-4 w-4" />
                Add Symbol
              </Button>
            }
          />
        </div>
      ) : (
        /* Table */
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/30 hover:bg-muted/30">
                <TableHead>Symbol</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">Change</TableHead>
                <TableHead className="text-right">Target</TableHead>
                <TableHead>Notes</TableHead>
                <TableHead className="w-10"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredWatchlist.map((item) => (
                <TableRow key={item.id} className="hover:bg-muted/30">
                  <TableCell>
                    <span className="font-semibold">{item.asset.symbol}</span>
                  </TableCell>
                  <TableCell>
                    <span className="text-sm text-muted-foreground">
                      {item.asset.name}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    {item.currentPrice !== undefined ? (
                      <span className="font-mono">${item.currentPrice.toFixed(2)}</span>
                    ) : (
                      <span className="text-muted-foreground text-sm">N/A</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {item.priceChangePercent !== undefined ? (
                      <PercentChange value={item.priceChangePercent} size="sm" />
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {item.targetPrice ? (
                      <div className="flex items-center justify-end gap-1">
                        <Target className="h-3 w-3 text-primary" />
                        <span className="font-mono text-sm">${item.targetPrice.toFixed(2)}</span>
                      </div>
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {item.notes ? (
                      <div className="flex items-center gap-1 text-sm text-muted-foreground">
                        <MessageSquare className="h-3 w-3" />
                        <span className="truncate max-w-[150px]">{item.notes}</span>
                      </div>
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-8 w-8">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="bg-popover">
                        <DropdownMenuItem onClick={() => openEditItemDialog(item)}>
                          <Edit2 className="h-4 w-4 mr-2" /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleConvertToHolding(item)}>
                          <Plus className="h-4 w-4 mr-2" /> Add to Holdings
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem 
                          className="text-destructive"
                          onClick={() => removeFromWatchlist(item.id)}
                        >
                          <Trash2 className="h-4 w-4 mr-2" /> Remove
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename Watchlist</DialogTitle>
            <DialogDescription>
              Enter a new name for this watchlist.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Input
              placeholder="Watchlist name..."
              value={newWatchlistName}
              onChange={(e) => setNewWatchlistName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleRenameWatchlist()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleRenameWatchlist} disabled={!newWatchlistName.trim()}>Rename</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Watchlist</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{watchlists.find(wl => wl.id === watchlistToEdit)?.name}"? 
              This will remove all items in this watchlist.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDeleteWatchlist}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Item Dialog */}
      <Dialog open={editItemDialogOpen} onOpenChange={setEditItemDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit {itemToEdit?.symbol}</DialogTitle>
            <DialogDescription>
              Update notes and target price for this asset.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Target Price</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
                <Input
                  type="number"
                  step="0.01"
                  placeholder="Enter target price..."
                  value={itemToEdit?.targetPrice || ''}
                  onChange={(e) => setItemToEdit(prev => prev ? { ...prev, targetPrice: e.target.value } : null)}
                  className="pl-7"
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Notes</label>
              <Input
                placeholder="Add notes..."
                value={itemToEdit?.notes || ''}
                onChange={(e) => setItemToEdit(prev => prev ? { ...prev, notes: e.target.value } : null)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditItemDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleUpdateItem}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
