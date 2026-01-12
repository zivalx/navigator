import { TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react';
import { StatCard } from '@/components/common/StatCard';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { Wallet, BarChart3, ArrowUpDown } from 'lucide-react';

export function PortfolioSummaryCards() {
  const { portfolioSummary, topMovers } = usePortfolio();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        title="Total NAV"
        value={portfolioSummary.totalNav}
        change={portfolioSummary.dailyPnLPercent}
        changeLabel="today"
        variant="primary"
        icon={<Wallet className="h-4 w-4" />}
      />
      <StatCard
        title="Daily P&L"
        value={portfolioSummary.dailyPnL}
        change={portfolioSummary.dailyPnLPercent}
        variant={portfolioSummary.dailyPnL >= 0 ? 'success' : 'danger'}
        icon={<ArrowUpDown className="h-4 w-4" />}
        compact={false}
      />
      <StatCard
        title="Unrealized P&L"
        value={portfolioSummary.totalUnrealizedPnL}
        change={portfolioSummary.totalUnrealizedPnLPercent}
        variant={portfolioSummary.totalUnrealizedPnL >= 0 ? 'success' : 'danger'}
        icon={<BarChart3 className="h-4 w-4" />}
        compact={false}
      />
      <StatCard
        title="Total Cost Basis"
        value={portfolioSummary.totalCost}
        icon={<Wallet className="h-4 w-4" />}
      />
    </div>
  );
}
