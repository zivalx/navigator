import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AssetType, MarketRegion } from '@/lib/types';

interface AssetBadgeProps {
  type: AssetType;
  className?: string;
}

export function AssetTypeBadge({ type, className }: AssetBadgeProps) {
  const config: Record<AssetType, { label: string; className: string }> = {
    stock: { label: 'Stock', className: 'bg-primary/10 text-primary border-primary/20' },
    etf: { label: 'ETF', className: 'bg-accent/10 text-accent border-accent/20' },
    crypto: { label: 'Crypto', className: 'bg-warning/10 text-warning border-warning/20' },
    fund: { label: 'Fund', className: 'bg-success/10 text-success border-success/20' },
    index: { label: 'Index', className: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' },
    other: { label: 'Other', className: 'bg-muted/50 text-muted-foreground border-muted' },
  };

  const { label, className: badgeClass } = config[type] ?? {
    label: String(type),
    className: 'bg-muted/50 text-muted-foreground border-muted',
  };

  return (
    <Badge variant="outline" className={cn('text-xs font-medium', badgeClass, className)}>
      {label}
    </Badge>
  );
}

interface RegionBadgeProps {
  region: MarketRegion;
  className?: string;
}

export function RegionBadge({ region, className }: RegionBadgeProps) {
  const config: Record<MarketRegion, { label: string; flag: string }> = {
    IL: { label: 'Israel', flag: '🇮🇱' },
    US: { label: 'US', flag: '🇺🇸' },
    EU: { label: 'EU', flag: '🇪🇺' },
    ASIA: { label: 'Asia', flag: '🌏' },
  };

  const { label, flag } = config[region] ?? { label: String(region), flag: '' };

  return (
    <Badge variant="outline" className={cn('text-xs font-medium gap-1', className)}>
      {flag && <span>{flag}</span>}
      {label}
    </Badge>
  );
}
