import { AppLayout } from '@/components/layout/AppLayout';
import { PortfolioTable } from '@/components/portfolio/PortfolioTable';
import { AddHoldingDialog, CSVImportDialog } from '@/components/portfolio/AddHoldingDialog';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { CurrencyDisplay, PercentChange } from '@/components/common/PriceDisplay';

const Portfolio = () => {
  const { portfolioSummary } = usePortfolio();

  return (
    <AppLayout>
      <div className="space-y-6 animate-fade-in">
        <div className="flex flex-col sm:flex-row justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold mb-1">Portfolio</h1>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span>Total: <CurrencyDisplay value={portfolioSummary.totalNav} size="sm" /></span>
              <span className="flex items-center gap-1">
                P&L: <PercentChange value={portfolioSummary.totalUnrealizedPnLPercent} size="sm" />
              </span>
            </div>
          </div>
          <div className="flex gap-3">
            <CSVImportDialog />
            <AddHoldingDialog />
          </div>
        </div>

        <PortfolioTable />
      </div>
    </AppLayout>
  );
};

export default Portfolio;
