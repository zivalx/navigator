import { useState, useCallback } from 'react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Plus, Upload, AlertCircle, CheckCircle, Home, Wallet } from 'lucide-react';
import { Currency, AssetType } from '@/lib/types';
import { getAssetBySymbol, mockAssets } from '@/lib/mockData';
import { toast } from 'sonner';

export function AddHoldingDialog() {
  const { addHolding, addCustomAsset, assets } = usePortfolio();
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'asset' | 'other'>('asset');
  
  // Asset form state
  const [symbol, setSymbol] = useState('');
  const [quantity, setQuantity] = useState('');
  const [avgCost, setAvgCost] = useState('');
  const [accountName, setAccountName] = useState('Main');
  const [purchaseDate, setPurchaseDate] = useState(new Date().toISOString().split('T')[0]);
  const [error, setError] = useState('');

  // Other asset form state
  const [otherName, setOtherName] = useState('');
  const [otherQuantity, setOtherQuantity] = useState('1');
  const [otherCost, setOtherCost] = useState('');
  const [otherCurrentPrice, setOtherCurrentPrice] = useState('');
  const [otherAccountName, setOtherAccountName] = useState('');
  const [otherPurchaseDate, setOtherPurchaseDate] = useState(new Date().toISOString().split('T')[0]);
  const [otherCurrency, setOtherCurrency] = useState<Currency>('USD');

  const handleSubmitAsset = () => {
    setError('');
    
    const asset = getAssetBySymbol(symbol);
    if (!asset) {
      setError('Asset not found. Try AAPL, MSFT, GOOGL, etc.');
      return;
    }

    const qty = parseFloat(quantity);
    const cost = parseFloat(avgCost);

    if (isNaN(qty) || qty <= 0) {
      setError('Please enter a valid quantity');
      return;
    }

    if (isNaN(cost) || cost <= 0) {
      setError('Please enter a valid average cost');
      return;
    }

    addHolding({
      assetId: asset.id,
      quantity: qty,
      avgCost: cost,
      costCurrency: asset.currency,
      accountName,
      tags: [],
      purchaseDate: new Date(purchaseDate),
    });

    toast.success(`Added ${qty} ${symbol} to your portfolio`);
    setOpen(false);
    resetForm();
  };

  const handleSubmitOther = () => {
    setError('');

    if (!otherName.trim()) {
      setError('Please enter an asset name');
      return;
    }

    const qty = parseFloat(otherQuantity);
    const cost = parseFloat(otherCost);
    const currentPrice = parseFloat(otherCurrentPrice);

    if (isNaN(qty) || qty <= 0) {
      setError('Please enter a valid quantity');
      return;
    }

    if (isNaN(cost) || cost <= 0) {
      setError('Please enter a valid cost');
      return;
    }

    if (isNaN(currentPrice) || currentPrice <= 0) {
      setError('Please enter a valid current price');
      return;
    }

    addCustomAsset(
      {
        symbol: otherName.substring(0, 10).toUpperCase().replace(/\s+/g, '_'),
        name: otherName.trim(),
        currency: otherCurrency,
        assetType: 'other',
        marketRegion: 'US',
      },
      {
        quantity: qty,
        avgCost: cost,
        currentPrice,
        accountName: otherAccountName || 'Other',
        purchaseDate: new Date(otherPurchaseDate),
      }
    );

    toast.success(`Added ${otherName} to your portfolio`);
    setOpen(false);
    resetForm();
  };

  const resetForm = () => {
    setSymbol('');
    setQuantity('');
    setAvgCost('');
    setAccountName('Main');
    setPurchaseDate(new Date().toISOString().split('T')[0]);
    setOtherName('');
    setOtherQuantity('1');
    setOtherCost('');
    setOtherCurrentPrice('');
    setOtherAccountName('');
    setOtherPurchaseDate(new Date().toISOString().split('T')[0]);
    setOtherCurrency('USD');
    setError('');
    setActiveTab('asset');
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2">
          <Plus className="h-4 w-4" />
          Add Holding
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Add New Holding</DialogTitle>
          <DialogDescription>
            Add a market asset or a custom holding like real estate or cash.
          </DialogDescription>
        </DialogHeader>
        
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'asset' | 'other')}>
          <TabsList className="grid grid-cols-2 w-full">
            <TabsTrigger value="asset" className="gap-2">
              <Wallet className="h-4 w-4" />
              Market Asset
            </TabsTrigger>
            <TabsTrigger value="other" className="gap-2">
              <Home className="h-4 w-4" />
              Other
            </TabsTrigger>
          </TabsList>

          <TabsContent value="asset" className="space-y-4 mt-4">
            <div className="space-y-2">
              <Label htmlFor="symbol">Symbol</Label>
              <Input
                id="symbol"
                placeholder="e.g., AAPL"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="quantity">Quantity</Label>
                <Input
                  id="quantity"
                  type="number"
                  placeholder="100"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="avgCost">Avg Cost</Label>
                <Input
                  id="avgCost"
                  type="number"
                  placeholder="150.00"
                  value={avgCost}
                  onChange={(e) => setAvgCost(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="purchaseDate">Purchase Date</Label>
              <Input
                id="purchaseDate"
                type="date"
                value={purchaseDate}
                onChange={(e) => setPurchaseDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account">Account / Broker</Label>
              <Select value={accountName} onValueChange={setAccountName}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Main">Main</SelectItem>
                  <SelectItem value="Interactive Brokers">Interactive Brokers</SelectItem>
                  <SelectItem value="Fidelity">Fidelity</SelectItem>
                  <SelectItem value="Schwab">Schwab</SelectItem>
                  <SelectItem value="IRA">IRA</SelectItem>
                  <SelectItem value="Coinbase">Coinbase</SelectItem>
                  <SelectItem value="Other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </TabsContent>

          <TabsContent value="other" className="space-y-4 mt-4">
            <div className="space-y-2">
              <Label htmlFor="otherName">Asset Name</Label>
              <Input
                id="otherName"
                placeholder="e.g., Primary Residence, Cash Savings"
                value={otherName}
                onChange={(e) => setOtherName(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="otherCost">Cost Basis</Label>
                <Input
                  id="otherCost"
                  type="number"
                  placeholder="500000"
                  value={otherCost}
                  onChange={(e) => setOtherCost(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="otherCurrentPrice">Current Value</Label>
                <Input
                  id="otherCurrentPrice"
                  type="number"
                  placeholder="650000"
                  value={otherCurrentPrice}
                  onChange={(e) => setOtherCurrentPrice(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="otherQuantity">Quantity</Label>
                <Input
                  id="otherQuantity"
                  type="number"
                  placeholder="1"
                  value={otherQuantity}
                  onChange={(e) => setOtherQuantity(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="otherCurrency">Currency</Label>
                <Select value={otherCurrency} onValueChange={(v) => setOtherCurrency(v as Currency)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="USD">USD</SelectItem>
                    <SelectItem value="EUR">EUR</SelectItem>
                    <SelectItem value="GBP">GBP</SelectItem>
                    <SelectItem value="CHF">CHF</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="otherPurchaseDate">Purchase Date</Label>
              <Input
                id="otherPurchaseDate"
                type="date"
                value={otherPurchaseDate}
                onChange={(e) => setOtherPurchaseDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="otherAccountName">Location / Notes</Label>
              <Input
                id="otherAccountName"
                placeholder="e.g., Bank of America, Manhattan property"
                value={otherAccountName}
                onChange={(e) => setOtherAccountName(e.target.value)}
              />
            </div>
          </TabsContent>
        </Tabs>

        {error && (
          <div className="flex items-center gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={activeTab === 'asset' ? handleSubmitAsset : handleSubmitOther}>
            Add Holding
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CSVImportDialog() {
  const { importHoldings } = usePortfolio();
  const [open, setOpen] = useState(false);
  const [csvData, setCsvData] = useState('');
  const [preview, setPreview] = useState<any[]>([]);
  const [error, setError] = useState('');

  const parseCSV = useCallback((csv: string) => {
    setError('');
    const lines = csv.trim().split('\n');
    if (lines.length < 2) {
      setError('CSV must have header row and at least one data row');
      return;
    }

    const headers = lines[0].toLowerCase().split(',').map(h => h.trim());
    const symbolIdx = headers.findIndex(h => h.includes('symbol'));
    const qtyIdx = headers.findIndex(h => h.includes('qty') || h.includes('quantity'));
    const costIdx = headers.findIndex(h => h.includes('cost') || h.includes('price'));

    if (symbolIdx === -1 || qtyIdx === -1 || costIdx === -1) {
      setError('CSV must contain columns: symbol, quantity/qty, avgCost/cost/price');
      return;
    }

    const holdings = [];
    for (let i = 1; i < lines.length; i++) {
      const values = lines[i].split(',').map(v => v.trim());
      const symbol = values[symbolIdx]?.toUpperCase();
      const quantity = parseFloat(values[qtyIdx]);
      const avgCost = parseFloat(values[costIdx]);

      const asset = getAssetBySymbol(symbol);
      if (asset && !isNaN(quantity) && !isNaN(avgCost)) {
        holdings.push({
          symbol,
          assetId: asset.id,
          quantity,
          avgCost,
          costCurrency: asset.currency,
          accountName: 'Imported',
          valid: true,
        });
      } else {
        holdings.push({
          symbol,
          quantity,
          avgCost,
          valid: false,
          error: !asset ? 'Unknown symbol' : 'Invalid data',
        });
      }
    }

    setPreview(holdings);
  }, []);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        setCsvData(content);
        parseCSV(content);
      };
      reader.readAsText(file);
    }
  };

  const handleImport = () => {
    const validHoldings = preview.filter(h => h.valid).map(h => ({
      assetId: h.assetId,
      quantity: h.quantity,
      avgCost: h.avgCost,
      costCurrency: h.costCurrency as Currency,
      accountName: h.accountName,
      tags: ['imported'],
      purchaseDate: new Date(),
    }));

    if (validHoldings.length === 0) {
      setError('No valid holdings to import');
      return;
    }

    importHoldings(validHoldings);
    toast.success(`Imported ${validHoldings.length} holdings`);
    setOpen(false);
    setCsvData('');
    setPreview([]);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-2">
          <Upload className="h-4 w-4" />
          Import CSV
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import Holdings from CSV</DialogTitle>
          <DialogDescription>
            Upload a CSV file with columns: symbol, quantity, avgCost
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="border-2 border-dashed border-border rounded-lg p-6 text-center">
            <Input
              type="file"
              accept=".csv"
              onChange={handleFileUpload}
              className="max-w-xs mx-auto"
            />
            <p className="text-sm text-muted-foreground mt-2">
              Or paste CSV content below
            </p>
          </div>

          <textarea
            className="w-full h-32 p-3 rounded-lg border border-border bg-muted/30 font-mono text-sm"
            placeholder="symbol,quantity,avgCost&#10;AAPL,50,145.50&#10;MSFT,30,280.00"
            value={csvData}
            onChange={(e) => {
              setCsvData(e.target.value);
              if (e.target.value) parseCSV(e.target.value);
            }}
          />

          {preview.length > 0 && (
            <div className="border border-border rounded-lg overflow-hidden">
              <div className="bg-muted/30 px-4 py-2 font-medium text-sm">
                Preview ({preview.filter(h => h.valid).length} valid, {preview.filter(h => !h.valid).length} invalid)
              </div>
              <div className="max-h-48 overflow-y-auto">
                {preview.map((h, i) => (
                  <div 
                    key={i} 
                    className={`flex items-center justify-between px-4 py-2 border-t border-border ${
                      h.valid ? '' : 'bg-destructive/5'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {h.valid ? (
                        <CheckCircle className="h-4 w-4 text-success" />
                      ) : (
                        <AlertCircle className="h-4 w-4 text-destructive" />
                      )}
                      <span className="font-semibold">{h.symbol}</span>
                      <span className="text-muted-foreground">
                        {h.quantity} @ ${h.avgCost}
                      </span>
                    </div>
                    {!h.valid && (
                      <span className="text-sm text-destructive">{h.error}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={handleImport} disabled={preview.filter(h => h.valid).length === 0}>
            Import {preview.filter(h => h.valid).length} Holdings
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
