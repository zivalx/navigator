import { usePortfolio } from '@/contexts/PortfolioContext';
import { PriceDisplay, PercentChange, CurrencyDisplay } from '@/components/common/PriceDisplay';
import { Sparkline } from '@/components/common/Sparkline';
import { AssetTypeBadge } from '@/components/common/AssetBadge';
import { InfoTooltip } from '@/components/common/InfoTooltip';
import { cn } from '@/lib/utils';

export function TopHoldingsCard() {
  const { topHoldings } = usePortfolio();

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <h3 className="text-lg font-semibold mb-4">
        Top Holdings
        <InfoTooltip text="Your largest positions by market value, with weight as % of total portfolio." />
      </h3>
      <div className="space-y-3">
        {topHoldings.map((holding, index) => (
          <div 
            key={holding.id}
            className="flex items-center gap-4 p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors cursor-pointer"
            style={{ animationDelay: `${index * 50}ms` }}
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold">{holding.asset.symbol}</span>
                <AssetTypeBadge type={holding.asset.assetType} />
              </div>
              <span className="text-sm text-muted-foreground truncate block">
                {holding.asset.name}
              </span>
            </div>
            <div className="hidden sm:block">
              <Sparkline 
                positive={(holding.priceChangePercent ?? 0) >= 0}
                width={60}
                height={20}
              />
            </div>
            <div className="text-right">
              <CurrencyDisplay value={holding.marketValue ?? 0} size="sm" />
              <div className="flex items-center justify-end gap-2 mt-1">
                <span className="text-xs text-muted-foreground">
                  {holding.weight?.toFixed(1)}%
                </span>
                {holding.priceChangePercent !== undefined && (
                  <PercentChange value={holding.priceChangePercent} size="sm" />
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function TopMoversCard() {
  const { topMovers } = usePortfolio();

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <h3 className="text-lg font-semibold mb-4">
        Today's Movers
        <InfoTooltip text="Real market-wide top gainers and losers across all US-listed stocks today, ranked by % change." />
      </h3>
      
      <div className="space-y-4">
        {/* Gainers */}
        <div>
          <h4 className="text-sm font-medium text-success mb-2 flex items-center gap-1">
            <span className="text-xs">▲</span> Top Gainers
          </h4>
          <div className="space-y-2">
            {topMovers.gainers.map((mover, index) => (
              <div 
                key={mover.asset.id}
                className="flex items-center justify-between p-2 rounded-lg bg-success/5 border border-success/10"
              >
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm">{mover.asset.symbol}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono text-sm">${mover.price.toFixed(2)}</span>
                  <PercentChange value={mover.changePercent} size="sm" className="ml-2" />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Losers */}
        <div>
          <h4 className="text-sm font-medium text-destructive mb-2 flex items-center gap-1">
            <span className="text-xs">▼</span> Top Losers
          </h4>
          <div className="space-y-2">
            {topMovers.losers.map((mover, index) => (
              <div 
                key={mover.asset.id}
                className="flex items-center justify-between p-2 rounded-lg bg-destructive/5 border border-destructive/10"
              >
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm">{mover.asset.symbol}</span>
                </div>
                <div className="text-right">
                  <span className="font-mono text-sm">${mover.price.toFixed(2)}</span>
                  <PercentChange value={mover.changePercent} size="sm" className="ml-2" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
