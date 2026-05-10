import { AppLayout } from '@/components/layout/AppLayout';
import { WatchlistTable } from '@/components/watchlist/WatchlistTable';

const Watchlist = () => {
  return (
    <AppLayout>
      <div className="space-y-4 animate-fade-in">
        <WatchlistTable />
      </div>
    </AppLayout>
  );
};

export default Watchlist;
