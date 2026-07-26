import { usePortfolio } from '@/contexts/PortfolioContext';
import { useAppSettings, RefreshIntervalSeconds } from '@/contexts/AppSettingsContext';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Currency } from '@/lib/types';

export function SettingsPage() {
  const { baseCurrency, setBaseCurrency } = usePortfolio();
  const { refreshInterval, setRefreshInterval, compactNumbers, setCompactNumbers } = useAppSettings();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-2">Settings</h1>
        <p className="text-muted-foreground">Configure your dashboard preferences.</p>
      </div>

      <div className="rounded-xl border border-border bg-card p-6 space-y-6">
        <h3 className="text-lg font-semibold">Display Settings</h3>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="baseCurrency">Base Currency</Label>
            <Select value={baseCurrency} onValueChange={(v) => setBaseCurrency(v as Currency)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="USD">USD - US Dollar</SelectItem>
                <SelectItem value="EUR">EUR - Euro</SelectItem>
                <SelectItem value="GBP">GBP - British Pound</SelectItem>
                <SelectItem value="JPY">JPY - Japanese Yen</SelectItem>
                <SelectItem value="CHF">CHF - Swiss Franc</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              All portfolio values will be converted to this currency.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="refreshInterval">Auto-refresh Interval</Label>
            <Select
              value={String(refreshInterval)}
              onValueChange={(v) => setRefreshInterval(Number(v) as RefreshIntervalSeconds)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="30">Every 30 seconds</SelectItem>
                <SelectItem value="60">Every 1 minute</SelectItem>
                <SelectItem value="300">Every 5 minutes</SelectItem>
                <SelectItem value="0">Manual only</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              How often to fetch new price data.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <Label>Compact Number Format</Label>
            <p className="text-xs text-muted-foreground">
              Show values like $1.2M instead of $1,200,000.
            </p>
          </div>
          <Switch checked={compactNumbers} onCheckedChange={setCompactNumbers} />
        </div>
      </div>
    </div>
  );
}
