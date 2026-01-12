import { usePortfolio } from '@/contexts/PortfolioContext';
import { ExternalLink, TrendingUp, TrendingDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

export function NewsDigestCard() {
  const { news } = usePortfolio();

  const getSentimentIcon = (score: number) => {
    if (score > 0.3) return <TrendingUp className="h-4 w-4 text-success" />;
    if (score < -0.3) return <TrendingDown className="h-4 w-4 text-destructive" />;
    return null;
  };

  const getSentimentClass = (score: number) => {
    if (score > 0.3) return 'border-l-success';
    if (score < -0.3) return 'border-l-destructive';
    return 'border-l-muted-foreground';
  };

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <h3 className="text-lg font-semibold mb-4">What Changed Today</h3>
      <div className="space-y-3">
        {news.slice(0, 5).map((item) => (
          <a
            key={item.id}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              'block p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors border-l-2',
              getSentimentClass(item.sentimentScore)
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  {getSentimentIcon(item.sentimentScore)}
                  <span className="text-xs font-medium text-primary">
                    {item.assetId ? `Related Asset` : 'Market News'}
                  </span>
                </div>
                <h4 className="font-medium text-sm leading-tight line-clamp-2">
                  {item.title}
                </h4>
                <div className="flex items-center gap-2 mt-2 text-xs text-muted-foreground">
                  <span>{item.publisher}</span>
                  <span>•</span>
                  <span>{formatDistanceToNow(item.publishedAt, { addSuffix: true })}</span>
                </div>
              </div>
              <ExternalLink className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
