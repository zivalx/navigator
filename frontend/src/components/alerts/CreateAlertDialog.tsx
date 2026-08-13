import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import type { PriceAlert, PriceAlertIntent, PriceAlertRule } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatMoney } from './alertUtils';

export interface AlertAssetContext {
  assetId?: string;
  symbol: string;
  name?: string;
  currentPrice?: number;
}

interface CreateAlertDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-locked asset context, e.g. from a Portfolio/Watchlist row's "Add alert" action. */
  asset?: AlertAssetContext;
  /** When set, the dialog edits this alert instead of creating a new one. */
  editAlert?: PriceAlert | null;
}

type SearchResult = { id?: string; symbol: string; name?: string };
type TrailType = 'percent' | 'amount';
type IntentChoice = 'none' | PriceAlertIntent;

export function CreateAlertDialog({ open, onOpenChange, asset, editAlert }: CreateAlertDialogProps) {
  const queryClient = useQueryClient();
  const isEdit = !!editAlert;
  const isLocked = !!asset && !isEdit;

  const [selectedAsset, setSelectedAsset] = useState<AlertAssetContext | null>(null);
  const [symbolQuery, setSymbolQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [rule, setRule] = useState<PriceAlertRule>('price_below');
  const [threshold, setThreshold] = useState('');
  const [trailType, setTrailType] = useState<TrailType>('percent');
  const [trailValue, setTrailValue] = useState('');
  const [intent, setIntent] = useState<IntentChoice>('none');
  const [note, setNote] = useState('');
  const searchTimeout = useRef<ReturnType<typeof setTimeout>>();

  const isTrailing = rule === 'trailing_stop';

  // (Re)initialize fields every time the dialog is opened.
  useEffect(() => {
    if (!open) return;
    if (editAlert) {
      setSelectedAsset({ assetId: editAlert.assetId, symbol: editAlert.symbol, name: editAlert.name });
      setRule(editAlert.rule);
      setThreshold(editAlert.threshold != null ? editAlert.threshold.toString() : '');
      setTrailType(editAlert.trailAmount != null ? 'amount' : 'percent');
      setTrailValue(
        editAlert.trailPercent != null
          ? editAlert.trailPercent.toString()
          : editAlert.trailAmount != null
            ? editAlert.trailAmount.toString()
            : ''
      );
      setIntent(editAlert.intent ?? 'none');
      setNote(editAlert.note || '');
    } else {
      setSelectedAsset(asset ?? null);
      setRule('price_below');
      setThreshold('');
      setTrailType('percent');
      setTrailValue('');
      setIntent('none');
      setNote('');
    }
    setSymbolQuery('');
    setSearchResults([]);
  }, [open, asset, editAlert]);

  const handleSearch = (query: string) => {
    setSymbolQuery(query);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (query.length < 1) {
      setSearchResults([]);
      return;
    }
    searchTimeout.current = setTimeout(async () => {
      try {
        const results = await api.searchAssets(query);
        setSearchResults(results);
      } catch {
        setSearchResults([]);
      }
    }, 300);
  };

  const handleSelectSearchResult = async (result: SearchResult) => {
    let currentPrice: number | undefined;
    try {
      const quote = await api.getQuote(result.symbol);
      currentPrice = quote?.price;
    } catch {
      // Presets just won't be available; the user can still enter a threshold manually.
    }
    setSelectedAsset({ assetId: result.id, symbol: result.symbol, name: result.name, currentPrice });
    setSymbolQuery('');
    setSearchResults([]);
  };

  const applyPreset = (presetRule: PriceAlertRule, pct: number) => {
    if (!selectedAsset?.currentPrice) return;
    const value = selectedAsset.currentPrice * (1 + pct);
    setRule(presetRule);
    setThreshold(value.toFixed(2));
  };

  const applyTrailPreset = (pct: number) => {
    setTrailType('percent');
    setTrailValue(pct.toString());
  };

  const mutation = useMutation({
    mutationFn: async () => {
      const thresholdNum = parseFloat(threshold);
      const trailNum = parseFloat(trailValue);
      const effectiveIntent: PriceAlertIntent | undefined = isTrailing
        ? 'sell'
        : intent === 'none'
          ? undefined
          : intent;
      if (isEdit && editAlert) {
        return api.updateAlert(editAlert.id, {
          rule,
          threshold: isTrailing ? null : thresholdNum,
          intent: effectiveIntent,
          trailPercent: isTrailing && trailType === 'percent' ? trailNum : null,
          trailAmount: isTrailing && trailType === 'amount' ? trailNum : null,
          note: note || undefined,
        });
      }
      if (!selectedAsset) throw new Error('Select an asset first');
      return api.createAlert({
        assetId: selectedAsset.assetId,
        symbol: selectedAsset.assetId ? undefined : selectedAsset.symbol,
        rule,
        threshold: isTrailing ? undefined : thresholdNum,
        intent: effectiveIntent,
        trailPercent: isTrailing && trailType === 'percent' ? trailNum : undefined,
        trailAmount: isTrailing && trailType === 'amount' ? trailNum : undefined,
        note: note || undefined,
      });
    },
    onSuccess: (savedAlert) => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      toast.success(isEdit ? `Updated alert for ${savedAlert.symbol}` : `Alert created for ${savedAlert.symbol}`);
      onOpenChange(false);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : 'Failed to save alert');
    },
  });

  const thresholdNum = parseFloat(threshold);
  const trailNum = parseFloat(trailValue);
  const trailValid =
    !isNaN(trailNum) && trailNum > 0 && (trailType === 'amount' || trailNum < 100);
  const canSubmit =
    (isEdit || !!selectedAsset) &&
    (isTrailing ? trailValid : !isNaN(thresholdNum) && thresholdNum > 0);

  // Where the stop would sit right now, from the live quote (create) or the
  // tracked high-water mark (edit).
  const previewBase = isEdit ? editAlert?.highWaterMark ?? null : selectedAsset?.currentPrice ?? null;
  const previewStop =
    isTrailing && trailValid && previewBase != null
      ? trailType === 'percent'
        ? previewBase * (1 - trailNum / 100)
        : previewBase - trailNum
      : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit Alert' : 'Create Alert'}</DialogTitle>
          <DialogDescription>
            {selectedAsset
              ? `Set a price trigger or trailing stop for ${selectedAsset.symbol}.`
              : 'Search for an asset, then set a price trigger or trailing stop.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Asset selection — only when creating without a pre-locked asset */}
          {!isLocked && !isEdit && (
            <div className="space-y-2">
              <Label>Symbol</Label>
              {selectedAsset ? (
                <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <div>
                    <span className="font-semibold">{selectedAsset.symbol}</span>
                    {selectedAsset.name && (
                      <span className="ml-2 text-sm text-muted-foreground">{selectedAsset.name}</span>
                    )}
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => setSelectedAsset(null)}>
                    Change
                  </Button>
                </div>
              ) : (
                <>
                  <Input
                    placeholder="Search symbols..."
                    value={symbolQuery}
                    onChange={(e) => handleSearch(e.target.value)}
                  />
                  {searchResults.length > 0 && (
                    <div className="mt-2 border border-border rounded-lg overflow-hidden max-h-48 overflow-y-auto bg-popover">
                      {searchResults.map((result, idx) => (
                        <button
                          key={result.id || `${result.symbol}-${idx}`}
                          type="button"
                          className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-muted/50 transition-colors border-b border-border last:border-0 text-left"
                          onClick={() => handleSelectSearchResult(result)}
                        >
                          <span className="font-semibold">{result.symbol}</span>
                          <span className="text-sm text-muted-foreground truncate ml-3">{result.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {(selectedAsset || isEdit) && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Rule</Label>
                  <Select value={rule} onValueChange={(v) => setRule(v as PriceAlertRule)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="price_below">Price falls below</SelectItem>
                      <SelectItem value="price_above">Price rises above</SelectItem>
                      <SelectItem value="trailing_stop">Trailing stop</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {isTrailing ? (
                  <div className="space-y-2">
                    <Label htmlFor="alert-trail">Trail</Label>
                    <div className="flex gap-1.5">
                      <div className="relative flex-1">
                        {trailType === 'amount' && (
                          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
                        )}
                        <Input
                          id="alert-trail"
                          type="number"
                          step={trailType === 'percent' ? '0.1' : '0.01'}
                          value={trailValue}
                          onChange={(e) => setTrailValue(e.target.value)}
                          placeholder={trailType === 'percent' ? '8' : '15.00'}
                          className={trailType === 'amount' ? 'pl-7' : undefined}
                        />
                      </div>
                      <Select value={trailType} onValueChange={(v) => setTrailType(v as TrailType)}>
                        <SelectTrigger className="w-16 shrink-0">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="percent">%</SelectItem>
                          <SelectItem value="amount">$</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="alert-threshold">Threshold</Label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
                      <Input
                        id="alert-threshold"
                        type="number"
                        step="0.01"
                        value={threshold}
                        onChange={(e) => setThreshold(e.target.value)}
                        placeholder="0.00"
                        className="pl-7"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Intent — what the notification tells you to do */}
              <div className="space-y-2">
                <Label>Action when triggered</Label>
                {isTrailing ? (
                  <div className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">
                    🔴 Sell — trailing stops protect a position
                  </div>
                ) : (
                  <Select value={intent} onValueChange={(v) => setIntent(v as IntentChoice)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Just notify</SelectItem>
                      <SelectItem value="buy">🟢 Buy signal</SelectItem>
                      <SelectItem value="sell">🔴 Sell signal</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              </div>

              {isTrailing && (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Quick trails</Label>
                  <div className="flex gap-2 flex-wrap">
                    {[5, 8, 10].map((pct) => (
                      <Button key={pct} type="button" variant="outline" size="sm" onClick={() => applyTrailPreset(pct)}>
                        {pct}%
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              {!isTrailing && selectedAsset?.currentPrice !== undefined && (
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">
                    Quick presets (current price ${selectedAsset.currentPrice.toFixed(2)})
                  </Label>
                  <div className="flex gap-2 flex-wrap">
                    <Button type="button" variant="outline" size="sm" onClick={() => applyPreset('price_below', -0.05)}>
                      Stop loss −5%
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => applyPreset('price_below', -0.10)}>
                      Stop loss −10%
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => applyPreset('price_above', 0.10)}>
                      Target +10%
                    </Button>
                  </div>
                </div>
              )}

              {previewStop != null && previewBase != null && (
                <div className="rounded-md bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
                  Stop starts at <span className="font-mono font-medium text-foreground">{formatMoney(previewStop)}</span>{' '}
                  (from {isEdit ? 'high' : 'current price'} {formatMoney(previewBase)}) and rises as the price makes new
                  highs.
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="alert-note">Note (optional)</Label>
                <Input
                  id="alert-note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. protect gains"
                />
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit || mutation.isPending}>
            {isEdit ? 'Save Changes' : 'Create Alert'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
