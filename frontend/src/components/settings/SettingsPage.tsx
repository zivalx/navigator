import { useState } from 'react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { 
  Settings, 
  DollarSign, 
  RefreshCw, 
  Database, 
  Key,
  Info
} from 'lucide-react';
import { Currency } from '@/lib/types';
import { toast } from 'sonner';

export function SettingsPage() {
  const { baseCurrency, setBaseCurrency } = usePortfolio();
  const [refreshInterval, setRefreshInterval] = useState('60');
  const [provider, setProvider] = useState('demo');
  const [apiKey, setApiKey] = useState('');

  const handleSave = () => {
    toast.success('Settings saved successfully');
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-2">Settings</h1>
        <p className="text-muted-foreground">Configure your dashboard preferences and data sources.</p>
      </div>

      <Tabs defaultValue="general" className="space-y-6">
        <TabsList>
          <TabsTrigger value="general" className="gap-2">
            <Settings className="h-4 w-4" /> General
          </TabsTrigger>
          <TabsTrigger value="data" className="gap-2">
            <Database className="h-4 w-4" /> Data Sources
          </TabsTrigger>
          <TabsTrigger value="api" className="gap-2">
            <Key className="h-4 w-4" /> API Keys
          </TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="space-y-6">
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
                <Select value={refreshInterval} onValueChange={setRefreshInterval}>
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
                <Label>Show Demo Banner</Label>
                <p className="text-xs text-muted-foreground">
                  Display a banner when using demo/simulated data.
                </p>
              </div>
              <Switch defaultChecked />
            </div>

            <div className="flex items-center justify-between">
              <div>
                <Label>Compact Number Format</Label>
                <p className="text-xs text-muted-foreground">
                  Show values like $1.2M instead of $1,200,000.
                </p>
              </div>
              <Switch defaultChecked />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="data" className="space-y-6">
          <div className="rounded-xl border border-border bg-card p-6 space-y-6">
            <h3 className="text-lg font-semibold">Market Data Provider</h3>
            
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Provider</Label>
                <Select value={provider} onValueChange={setProvider}>
                  <SelectTrigger className="w-full sm:w-[300px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="demo">Demo (Simulated Data)</SelectItem>
                    <SelectItem value="alphavantage">Alpha Vantage</SelectItem>
                    <SelectItem value="twelvedata">Twelve Data</SelectItem>
                    <SelectItem value="finnhub">Finnhub</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {provider !== 'demo' && (
                <div className="flex items-start gap-3 p-4 rounded-lg bg-primary/5 border border-primary/20">
                  <Info className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" />
                  <div className="text-sm">
                    <p className="font-medium text-primary">API Key Required</p>
                    <p className="text-muted-foreground mt-1">
                      To use {provider === 'alphavantage' ? 'Alpha Vantage' : provider === 'twelvedata' ? 'Twelve Data' : 'Finnhub'}, 
                      you'll need to add your API key in the API Keys tab.
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="border-t border-border pt-6">
              <h4 className="font-medium mb-4">News Provider</h4>
              <Select defaultValue="demo">
                <SelectTrigger className="w-full sm:w-[300px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="demo">Demo (Sample News)</SelectItem>
                  <SelectItem value="newsapi">NewsAPI</SelectItem>
                  <SelectItem value="finnhub">Finnhub</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="api" className="space-y-6">
          <div className="rounded-xl border border-border bg-card p-6 space-y-6">
            <h3 className="text-lg font-semibold">API Configuration</h3>
            
            <div className="flex items-start gap-3 p-4 rounded-lg bg-warning/10 border border-warning/20">
              <Info className="h-5 w-5 text-warning flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-medium text-warning">Secure Storage</p>
                <p className="text-muted-foreground mt-1">
                  API keys should be stored securely. In production, use environment variables 
                  or a secrets manager instead of storing them in the browser.
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="marketApiKey">Market Data API Key</Label>
                <Input
                  id="marketApiKey"
                  type="password"
                  placeholder="Enter your API key..."
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="newsApiKey">News API Key</Label>
                <Input
                  id="newsApiKey"
                  type="password"
                  placeholder="Enter your API key..."
                />
              </div>
            </div>
          </div>

          <div className="flex justify-end">
            <Button onClick={handleSave}>Save Settings</Button>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
