import { useState } from 'react';
import { Plus, Search } from 'lucide-react';
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
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useMarketCards } from '@/contexts/MarketCardsContext';
import { toast } from 'sonner';

const popularSymbols = [
  { symbol: 'AAPL', name: 'Apple Inc.' },
  { symbol: 'MSFT', name: 'Microsoft' },
  { symbol: 'GOOGL', name: 'Alphabet' },
  { symbol: 'AMZN', name: 'Amazon' },
  { symbol: 'NVDA', name: 'NVIDIA' },
  { symbol: 'TSLA', name: 'Tesla' },
  { symbol: 'META', name: 'Meta Platforms' },
  { symbol: 'BTC', name: 'Bitcoin' },
  { symbol: 'ETH', name: 'Ethereum' },
  { symbol: 'SPY', name: 'S&P 500 ETF' },
  { symbol: 'QQQ', name: 'Nasdaq 100 ETF' },
  { symbol: 'VTI', name: 'Total Stock Market ETF' },
];

export function AddMarketCardDialog() {
  const { addCard, cards } = useMarketCards();
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState('');
  const [name, setName] = useState('');
  const [region, setRegion] = useState<'US' | 'EU' | 'ASIA'>('US');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredSymbols = popularSymbols.filter(
    s =>
      s.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleQuickAdd = (sym: string, symName: string) => {
    if (cards.some(c => c.symbol === sym)) {
      toast.error(`${sym} is already in your market cards`);
      return;
    }
    addCard(sym, symName, region);
    toast.success(`Added ${sym} to market cards`);
  };

  const handleSubmit = () => {
    if (!symbol.trim()) {
      toast.error('Please enter a symbol');
      return;
    }
    if (cards.some(c => c.symbol === symbol.toUpperCase())) {
      toast.error(`${symbol.toUpperCase()} is already in your market cards`);
      return;
    }
    addCard(symbol, name || symbol, region);
    toast.success(`Added ${symbol.toUpperCase()} to market cards`);
    setSymbol('');
    setName('');
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-2">
          <Plus className="h-4 w-4" />
          Add Card
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Market Card</DialogTitle>
          <DialogDescription>
            Add a new asset to your market overview
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Quick Add Section */}
          <div>
            <Label className="text-sm text-muted-foreground mb-2 block">
              Quick Add Popular Assets
            </Label>
            <div className="relative mb-2">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search symbols..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>
            <div className="flex flex-wrap gap-2 max-h-32 overflow-y-auto">
              {filteredSymbols.map((s) => {
                const isAdded = cards.some(c => c.symbol === s.symbol);
                return (
                  <Button
                    key={s.symbol}
                    variant={isAdded ? 'secondary' : 'outline'}
                    size="sm"
                    disabled={isAdded}
                    onClick={() => handleQuickAdd(s.symbol, s.name)}
                    className="text-xs"
                  >
                    {s.symbol}
                    {isAdded && <span className="ml-1 opacity-50">✓</span>}
                  </Button>
                );
              })}
            </div>
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-background px-2 text-muted-foreground">
                Or add custom
              </span>
            </div>
          </div>

          {/* Custom Add Form */}
          <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="symbol">Symbol</Label>
                <Input
                  id="symbol"
                  placeholder="e.g., AAPL"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="name">Name (optional)</Label>
                <Input
                  id="name"
                  placeholder="e.g., Apple Inc."
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Region</Label>
              <Select value={region} onValueChange={(v) => setRegion(v as 'US' | 'EU' | 'ASIA')}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="US">🇺🇸 US</SelectItem>
                  <SelectItem value="EU">🇪🇺 Europe</SelectItem>
                  <SelectItem value="ASIA">🌏 Asia</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit}>Add Card</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
