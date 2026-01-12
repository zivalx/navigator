import { AppLayout } from '@/components/layout/AppLayout';
import { PortfolioSummaryCards } from '@/components/dashboard/PortfolioSummaryCards';
import { TopHoldingsCard, TopMoversCard } from '@/components/dashboard/TopHoldingsCard';
import { NewsDigestCard } from '@/components/dashboard/NewsDigestCard';
import { AlertsCard } from '@/components/dashboard/AlertsCard';

const Dashboard = () => {
  return (
    <AppLayout>
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold mb-1">Dashboard</h1>
          <p className="text-muted-foreground">Your portfolio at a glance</p>
        </div>

        <PortfolioSummaryCards />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <TopHoldingsCard />
            <NewsDigestCard />
          </div>
          <div className="space-y-6">
            <TopMoversCard />
            <AlertsCard />
          </div>
        </div>
      </div>
    </AppLayout>
  );
};

export default Dashboard;
