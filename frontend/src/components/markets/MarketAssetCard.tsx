import { useState } from 'react';
import { TrendingUp, TrendingDown, X, GripVertical, Settings2, ChevronLeft, ChevronRight } from 'lucide-react';
import { PercentChange } from '@/components/common/PriceDisplay';
import { MarketCardData, DataSource, dataSourceLabels } from '@/lib/marketCardTypes';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useMarketCards } from '@/contexts/MarketCardsContext';

interface MarketAssetCardProps {
  card: MarketCardData;
  isFirst?: boolean;
  isLast?: boolean;
}

export function MarketAssetCard({ card, isFirst, isLast }: MarketAssetCardProps) {
  const { removeCard, updateCard, moveCard, isEditMode } = useMarketCards();
  const isPositive = (card.changePercent ?? 0) >= 0;

  const handleSourceChange = (source: DataSource) => {
    updateCard(card.id, { dataSource: source });
  };

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
          
          {/* Settings Dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon" 
                className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Settings2 className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuLabel>Data Source</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {(Object.keys(dataSourceLabels) as DataSource[]).map((source) => (
                <DropdownMenuItem
                  key={source}
                  onClick={() => handleSourceChange(source)}
                  className={cn(card.dataSource === source && 'bg-accent')}
                >
                  {dataSourceLabels[source]}
                  {card.dataSource === source && (
                    <span className="ml-auto text-xs text-muted-foreground">✓</span>
                  )}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem 
                className="text-destructive focus:text-destructive"
                onClick={() => removeCard(card.id)}
              >
                Remove Card
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Price */}
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xl font-semibold">
          ${card.price?.toFixed(2) ?? '—'}
        </span>
        <PercentChange value={card.changePercent ?? 0} size="md" />
      </div>

      {/* Data Source Badge */}
      <div className="mt-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {dataSourceLabels[card.dataSource]}
        </span>
        
        {/* Move Controls in Edit Mode */}
        {isEditMode && (
          <div className="flex gap-1">
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
    </div>
  );
}
