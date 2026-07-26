import { useState, useMemo } from 'react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { CurrencyDisplay, PercentChange } from '@/components/common/PriceDisplay';
import { AssetTypeBadge, RegionBadge } from '@/components/common/AssetBadge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table';
import { 
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Search, MoreHorizontal, Pencil, Trash2, ChevronDown, ChevronRight, Calendar, Building2, Bell } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { CreateAlertDialog, AlertAssetContext } from '@/components/alerts/CreateAlertDialog';
import { cn } from '@/lib/utils';
import { AssetType, MarketRegion, HoldingWithAsset, GroupedHolding } from '@/lib/types';
import { format } from 'date-fns';

export function PortfolioTable() {
  const { groupedHoldings, removeHolding, updateHolding } = usePortfolio();
  const [searchQuery, setSearchQuery] = useState('');
  const [assetTypeFilter, setAssetTypeFilter] = useState<string>('all');
  const [locationFilter, setLocationFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('value');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [expandedAssets, setExpandedAssets] = useState<Set<string>>(new Set());
  
  // Edit holding dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [lotToEdit, setLotToEdit] = useState<HoldingWithAsset | null>(null);
  const [editQuantity, setEditQuantity] = useState('');
  const [editAvgCost, setEditAvgCost] = useState('');
  const [editAccountName, setEditAccountName] = useState('');
  const [editPurchaseDate, setEditPurchaseDate] = useState('');

  // Add alert dialog state
  const [alertDialogOpen, setAlertDialogOpen] = useState(false);
  const [alertAsset, setAlertAsset] = useState<AlertAssetContext | null>(null);

  const openAlertDialog = (group: GroupedHolding) => {
    setAlertAsset({
      assetId: group.assetId,
      symbol: group.asset.symbol,
      name: group.asset.name,
      currentPrice: group.currentPrice,
    });
    setAlertDialogOpen(true);
  };

  // Get unique locations from all holdings
  const allLocations = useMemo(() => {
    const locations = new Set<string>();
    groupedHoldings.forEach(group => {
      group.lots.forEach(lot => {
        if (lot.accountName) locations.add(lot.accountName);
      });
    });
    return Array.from(locations).sort();
  }, [groupedHoldings]);

  const toggleExpanded = (assetId: string) => {
    setExpandedAssets(prev => {
      const next = new Set(prev);
      if (next.has(assetId)) {
        next.delete(assetId);
      } else {
        next.add(assetId);
      }
      return next;
    });
  };

  const openEditDialog = (lot: HoldingWithAsset) => {
    setLotToEdit(lot);
    setEditQuantity(lot.quantity.toString());
    setEditAvgCost(lot.avgCost.toString());
    setEditAccountName(lot.accountName);
    setEditPurchaseDate(format(new Date(lot.purchaseDate), 'yyyy-MM-dd'));
    setEditDialogOpen(true);
  };

  const handleUpdateHolding = () => {
    if (!lotToEdit) return;
    const quantity = parseFloat(editQuantity);
    const avgCost = parseFloat(editAvgCost);
    if (isNaN(quantity) || quantity <= 0 || isNaN(avgCost) || avgCost <= 0) return;
    
    updateHolding(lotToEdit.id, { 
      quantity, 
      avgCost,
      accountName: editAccountName,
      purchaseDate: new Date(editPurchaseDate),
    });
    setEditDialogOpen(false);
    setLotToEdit(null);
  };

  const filteredHoldings = useMemo(() => {
    let result = [...groupedHoldings];

    // Search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(h => 
        h.asset.symbol.toLowerCase().includes(query) ||
        h.asset.name.toLowerCase().includes(query)
      );
    }

    // Asset type filter
    if (assetTypeFilter !== 'all') {
      result = result.filter(h => h.asset.assetType === assetTypeFilter);
    }

    // Location filter (by account name)
    if (locationFilter !== 'all') {
      result = result.filter(h => 
        h.lots.some(lot => lot.accountName === locationFilter)
      );
    }

    // Sort
    result.sort((a, b) => {
      let comparison = 0;
      switch (sortBy) {
        case 'symbol':
          comparison = a.asset.symbol.localeCompare(b.asset.symbol);
          break;
        case 'value':
          comparison = (a.marketValue ?? 0) - (b.marketValue ?? 0);
          break;
        case 'pnl':
          comparison = (a.unrealizedPnL ?? 0) - (b.unrealizedPnL ?? 0);
          break;
        case 'change':
          comparison = (a.priceChangePercent ?? 0) - (b.priceChangePercent ?? 0);
          break;
        case 'weight':
          comparison = (a.weight ?? 0) - (b.weight ?? 0);
          break;
      }
      return sortOrder === 'desc' ? -comparison : comparison;
    });

    return result;
  }, [groupedHoldings, searchQuery, assetTypeFilter, locationFilter, sortBy, sortOrder]);

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortOrder('desc');
    }
  };

  const SortableHeader = ({ column, children }: { column: string; children: React.ReactNode }) => (
    <TableHead 
      className="cursor-pointer hover:bg-muted/50 transition-colors"
      onClick={() => handleSort(column)}
    >
      <div className="flex items-center gap-1">
        {children}
        {sortBy === column && (
          <span className="text-xs">{sortOrder === 'desc' ? '↓' : '↑'}</span>
        )}
      </div>
    </TableHead>
  );

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search holdings..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        <Select value={assetTypeFilter} onValueChange={setAssetTypeFilter}>
          <SelectTrigger className="w-full sm:w-[140px]">
            <SelectValue placeholder="Asset Type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            <SelectItem value="stock">Stocks</SelectItem>
            <SelectItem value="etf">ETFs</SelectItem>
            <SelectItem value="crypto">Crypto</SelectItem>
            <SelectItem value="fund">Funds</SelectItem>
            <SelectItem value="other">Other</SelectItem>
          </SelectContent>
        </Select>
        <Select value={locationFilter} onValueChange={setLocationFilter}>
          <SelectTrigger className="w-full sm:w-[160px]">
            <SelectValue placeholder="Location" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Locations</SelectItem>
            {allLocations.map(location => (
              <SelectItem key={location} value={location}>{location}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/30 hover:bg-muted/30">
                <TableHead className="w-8"></TableHead>
                <SortableHeader column="symbol">Symbol</SortableHeader>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Avg Cost</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <SortableHeader column="value">
                  <span className="ml-auto">Value</span>
                </SortableHeader>
                <SortableHeader column="pnl">
                  <span className="ml-auto">P&L</span>
                </SortableHeader>
                <SortableHeader column="change">
                  <span className="ml-auto">Change</span>
                </SortableHeader>
                <SortableHeader column="weight">
                  <span className="ml-auto">Weight</span>
                </SortableHeader>
                <TableHead className="w-10"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredHoldings.map((group) => {
                const isExpanded = expandedAssets.has(group.assetId);
                const hasMultipleLots = group.lots.length > 1;
                
                return (
                  <Collapsible key={group.assetId} open={isExpanded} onOpenChange={() => toggleExpanded(group.assetId)} asChild>
                    <>
                      {/* Main asset row */}
                      <TableRow 
                        className={cn(
                          "cursor-pointer transition-colors",
                          isExpanded 
                            ? "bg-primary/5 border-l-2 border-l-primary/50 hover:bg-primary/10" 
                            : "hover:bg-muted/30"
                        )} 
                        onClick={() => toggleExpanded(group.assetId)}
                      >
                        <TableCell className="p-2">
                          <CollapsibleTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={(e) => e.stopPropagation()}>
                              {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </Button>
                          </CollapsibleTrigger>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span className="font-semibold">{group.asset.symbol}</span>
                            {hasMultipleLots && (
                              <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                {group.lots.length} lots
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="text-sm text-muted-foreground truncate max-w-[200px] block">
                            {group.asset.name}
                          </span>
                        </TableCell>
                        <TableCell>
                          <AssetTypeBadge type={group.asset.assetType} />
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {group.totalQuantity.toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          ${group.avgCost.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          {group.currentPrice !== undefined ? (
                            <span className="font-mono">${group.currentPrice.toFixed(2)}</span>
                          ) : (
                            <span className="text-muted-foreground text-sm">N/A</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <CurrencyDisplay value={group.marketValue ?? 0} size="sm" />
                        </TableCell>
                        <TableCell className="text-right">
                          <div className={cn(
                            'font-mono text-sm',
                            (group.unrealizedPnL ?? 0) >= 0 ? 'text-success' : 'text-destructive'
                          )}>
                            {(group.unrealizedPnL ?? 0) >= 0 ? '+' : ''}
                            ${(group.unrealizedPnL ?? 0).toFixed(2)}
                          </div>
                        </TableCell>
                        <TableCell className="text-right">
                          {group.priceChangePercent !== undefined ? (
                            <PercentChange value={group.priceChangePercent} size="sm" />
                          ) : (
                            <span className="text-muted-foreground text-sm">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="text-sm text-muted-foreground">
                            {(group.weight ?? 0).toFixed(1)}%
                          </span>
                        </TableCell>
                        <TableCell onClick={(e) => e.stopPropagation()}></TableCell>
                      </TableRow>

                      {/* Expanded lot rows */}
                      <CollapsibleContent asChild>
                        <>
                          {group.lots.map((lot, idx) => (
                            <TableRow 
                              key={lot.id} 
                              className={cn(
                                "bg-primary/5 hover:bg-primary/10 border-l-2 border-l-primary/50",
                                idx === group.lots.length - 1 && "border-b border-b-primary/20"
                              )}
                            >
                              <TableCell></TableCell>
                              <TableCell>
                                <div className="flex items-center gap-1.5 text-sm text-muted-foreground pl-4">
                                  <Calendar className="h-3.5 w-3.5" />
                                  {format(new Date(lot.purchaseDate), 'MMM d, yyyy')}
                                </div>
                              </TableCell>
                              <TableCell>
                                <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                  <Building2 className="h-3.5 w-3.5" />
                                  {lot.accountName}
                                </div>
                              </TableCell>
                              <TableCell></TableCell>
                              <TableCell className="text-right font-mono text-sm">
                                {lot.quantity.toLocaleString()}
                              </TableCell>
                              <TableCell className="text-right font-mono text-sm">
                                ${lot.avgCost.toFixed(2)}
                              </TableCell>
                              <TableCell className="text-right text-sm text-muted-foreground">
                                —
                              </TableCell>
                              <TableCell className="text-right">
                                <span className="text-sm font-mono">
                                  ${(lot.marketValue ?? 0).toFixed(2)}
                                </span>
                              </TableCell>
                              <TableCell className="text-right">
                                <div className={cn(
                                  'font-mono text-sm',
                                  (lot.unrealizedPnL ?? 0) >= 0 ? 'text-success' : 'text-destructive'
                                )}>
                                  {(lot.unrealizedPnL ?? 0) >= 0 ? '+' : ''}
                                  ${(lot.unrealizedPnL ?? 0).toFixed(2)}
                                </div>
                              </TableCell>
                              <TableCell className="text-right text-sm text-muted-foreground">
                                {lot.unrealizedPnLPercent !== undefined ? (
                                  <PercentChange value={lot.unrealizedPnLPercent} size="sm" />
                                ) : '—'}
                              </TableCell>
                              <TableCell></TableCell>
                              <TableCell>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon" className="h-8 w-8">
                                      <MoreHorizontal className="h-4 w-4" />
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end">
                                    <DropdownMenuItem onClick={() => openEditDialog(lot)}>
                                      <Pencil className="h-4 w-4 mr-2" /> Edit Lot
                                    </DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => openAlertDialog(group)}>
                                      <Bell className="h-4 w-4 mr-2" /> Add Alert
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      className="text-destructive"
                                      onClick={() => removeHolding(lot.id)}
                                    >
                                      <Trash2 className="h-4 w-4 mr-2" /> Remove Lot
                                    </DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </TableCell>
                            </TableRow>
                          ))}
                        </>
                      </CollapsibleContent>
                    </>
                  </Collapsible>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* Edit Holding Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Transaction</DialogTitle>
            <DialogDescription>
              Update details for {lotToEdit?.asset.symbol} lot
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="edit-quantity">Quantity</Label>
                <Input
                  id="edit-quantity"
                  type="number"
                  step="any"
                  value={editQuantity}
                  onChange={(e) => setEditQuantity(e.target.value)}
                  placeholder="Enter quantity"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-avgcost">Cost per Unit</Label>
                <Input
                  id="edit-avgcost"
                  type="number"
                  step="0.01"
                  value={editAvgCost}
                  onChange={(e) => setEditAvgCost(e.target.value)}
                  placeholder="Enter cost"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-date">Purchase Date</Label>
              <Input
                id="edit-date"
                type="date"
                value={editPurchaseDate}
                onChange={(e) => setEditPurchaseDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-account">Account / Broker</Label>
              <Input
                id="edit-account"
                value={editAccountName}
                onChange={(e) => setEditAccountName(e.target.value)}
                placeholder="e.g., Interactive Brokers"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleUpdateHolding}>
              Save Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add Alert Dialog */}
      <CreateAlertDialog
        open={alertDialogOpen}
        onOpenChange={setAlertDialogOpen}
        asset={alertAsset ?? undefined}
      />
    </div>
  );
}