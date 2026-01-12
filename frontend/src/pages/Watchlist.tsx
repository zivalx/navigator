import { AppLayout } from '@/components/layout/AppLayout';
import { WatchlistTable } from '@/components/watchlist/WatchlistTable';

const Watchlist = () => {
  return (
    <AppLayout>
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold mb-1">Watchlist</h1>
          <p className="text-muted-foreground">Track assets you're interested in</p>
        </div>
        <WatchlistTable />
      </div>
    </AppLayout>
  );
};

export default Watchlist;
