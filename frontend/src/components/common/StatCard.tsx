import { cn } from '@/lib/utils';
import { CurrencyDisplay, PercentChange } from './PriceDisplay';
import { InfoTooltip } from './InfoTooltip';

interface StatCardProps {
  title: string;
  value: number;
  change?: number;
  changeLabel?: string;
  currency?: string;
  compact?: boolean;
  icon?: React.ReactNode;
  tooltip?: string;
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'danger';
  className?: string;
}

export function StatCard({
  title,
  value,
  change,
  changeLabel,
  currency = 'USD',
  compact,
  icon,
  tooltip,
  variant = 'default',
  className
}: StatCardProps) {
  const variantClasses = {
    default: 'bg-card border-border',
    primary: 'bg-card border-primary/20 glow-primary',
    success: 'bg-card border-success/20',
    warning: 'bg-card border-warning/20',
    danger: 'bg-card border-destructive/20',
  };

  return (
    <div className={cn(
      'rounded-xl border p-5 transition-all duration-200 hover:border-primary/30',
      variantClasses[variant],
      className
    )}>
      <div className="flex items-start justify-between mb-3">
        <span className="text-sm font-medium text-muted-foreground">
          {title}
          {tooltip && <InfoTooltip text={tooltip} />}
        </span>
        {icon && (
          <div className="p-2 rounded-lg bg-primary/10 text-primary">
            {icon}
          </div>
        )}
      </div>
      <div className="space-y-1">
        <CurrencyDisplay value={value} currency={currency} compact={compact} size="xl" />
        {change !== undefined && (
          <div className="flex items-center gap-2">
            <PercentChange value={change} size="sm" />
            {changeLabel && (
              <span className="text-xs text-muted-foreground">{changeLabel}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
