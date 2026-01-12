import { AppLayout } from '@/components/layout/AppLayout';
import TradingViewHeatmap from '@/components/heatmap/TradingViewHeatmap';

export default function Heatmap() {
  return (
    <AppLayout>
      <div className="h-[calc(100vh-theme(spacing.14)-theme(spacing.12))] flex flex-col">
        <div className="mb-4">
          <h1 className="text-2xl font-semibold">Market Heatmap</h1>
          <p className="text-muted-foreground text-sm">
            Visual overview of market performance by sector
          </p>
        </div>
        <div className="flex-1 rounded-lg border border-border overflow-hidden bg-card">
          <TradingViewHeatmap />
        </div>
      </div>
    </AppLayout>
  );
}
