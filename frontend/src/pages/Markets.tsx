import { AppLayout } from '@/components/layout/AppLayout';
import { MarketsOverview } from '@/components/markets/MarketsOverview';

const Markets = () => {
  return (
    <AppLayout>
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold mb-1">Markets</h1>
          <p className="text-muted-foreground">Global market overview and top movers</p>
        </div>
        <MarketsOverview />
      </div>
    </AppLayout>
  );
};

export default Markets;
