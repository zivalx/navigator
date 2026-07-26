import { TrendingUp, TrendingDown, X, GripVertical, ChevronLeft, ChevronRight } from 'lucide-react';
import { PercentChange } from '@/components/common/PriceDisplay';
import { MarketCardData } from '@/lib/marketCardTypes';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { useMarketCards } from '@/contexts/MarketCardsContext';

interface MarketAssetCardProps {
  card: MarketCardData;
  isFirst?: boolean;
  isLast?: boolean;
}

export function MarketAssetCard({ card, isFirst, isLast }: MarketAssetCardProps) {
  const { removeCard, moveCard, isEditMode } = useMarketCards();
  const isPositive = (card.changePercent ?? 0) >= 0;

  return (
    <div
      className={cn(
        'group relative rounded-xl border p-5 transition-all',
        isPositive
          ? 'bg-success/5 border-success/20 hover:border-success/40'
          : 'bg-destructive/5 border-destructive/20 hover:border-destructive/40',
        isEditMode && 'ring-2 ring-primary/30 cursor-move'
      )}
    >
      {/* Edit Mode Controls */}
      {isEditMode && (
        <div className="absolute -top-2 -right-2 flex gap-1">
          <Button
            size="icon"
            variant="destructive"
            className="h-6 w-6 rounded-full"
            onClick={() => removeCard(card.id)}
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      )}

      {/* Card Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          {isEditMode && (
            <GripVertical className="h-4 w-4 text-muted-foreground cursor-grab" />
          )}
          <div>
            <span className="font-semibold text-lg">{card.symbol}</span>
            <p className="text-sm text-muted-foreground line-clamp-1">{card.name}</p>
          </div>
        </div>
        
        <div className="flex items-center gap-1">
          {isPositive ? (
            <TrendingUp className="h-5 w-5 text-success" />
          ) : (
            <TrendingDown className="h-5 w-5 text-destructive" />
          )}

          {/* Quick remove (outside edit mode) */}
          {!isEditMode && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
              onClick={() => removeCard(card.id)}
              title="Remove card"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Price */}
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xl font-semibold">
          ${card.price?.toFixed(2) ?? '—'}
        </span>
        <PercentChange value={card.changePercent ?? 0} size="md" />
      </div>

      {/* Move Controls in Edit Mode */}
      {isEditMode && (
        <div className="mt-3 flex items-center justify-end gap-1">
          <Button
            size="icon"
            variant="ghost"
            className="h-5 w-5"
            onClick={() => moveCard(card.id, 'left')}
            disabled={isFirst}
          >
            <ChevronLeft className="h-3 w-3" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-5 w-5"
            onClick={() => moveCard(card.id, 'right')}
            disabled={isLast}
          >
            <ChevronRight className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
