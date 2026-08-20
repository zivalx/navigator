import { TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react';
import { StatCard } from '@/components/common/StatCard';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { Wallet, BarChart3, ArrowUpDown } from 'lucide-react';

export function PortfolioSummaryCards() {
  const { portfolioSummary, baseCurrency } = usePortfolio();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        title="Total NAV"
        tooltip="Net Asset Value — the current market value of all your holdings combined."
        value={portfolioSummary.totalNav}
        currency={baseCurrency}
        change={portfolioSummary.dailyPnLPercent}
        changeLabel="today"
        variant="primary"
        icon={<Wallet className="h-4 w-4" />}
      />
      <StatCard
        title="Daily P&L"
        tooltip="Profit or loss since today's market open, based on live price changes."
        value={portfolioSummary.dailyPnL}
        currency={baseCurrency}
        change={portfolioSummary.dailyPnLPercent}
        variant={portfolioSummary.dailyPnL >= 0 ? 'success' : 'danger'}
        icon={<ArrowUpDown className="h-4 w-4" />}
        compact={false}
      />
      <StatCard
        title="Unrealized P&L"
        tooltip="Total gain or loss on open positions vs. your average cost basis. Only realized when you sell."
        value={portfolioSummary.totalUnrealizedPnL}
        currency={baseCurrency}
        change={portfolioSummary.totalUnrealizedPnLPercent}
        variant={portfolioSummary.totalUnrealizedPnL >= 0 ? 'success' : 'danger'}
        icon={<BarChart3 className="h-4 w-4" />}
        compact={false}
      />
      <StatCard
        title="Total Cost Basis"
        tooltip="The total amount you originally invested across all holdings."
        value={portfolioSummary.totalCost}
        currency={baseCurrency}
        icon={<Wallet className="h-4 w-4" />}
      />
    </div>
  );
}
