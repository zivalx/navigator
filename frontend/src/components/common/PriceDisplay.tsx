import { cn } from '@/lib/utils';
import { useAppSettings } from '@/contexts/AppSettingsContext';

interface PriceDisplayProps {
  value: number;
  currency?: string;
  change?: number;
  changePercent?: number;
  showChange?: boolean;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

export function PriceDisplay({ 
  value, 
  currency = 'USD', 
  change, 
  changePercent, 
  showChange = true,
  size = 'md',
  className 
}: PriceDisplayProps) {
  const isPositive = (change ?? 0) >= 0;
  
  const formatPrice = (price: number) => {
    if (price >= 1000) {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(price);
    }
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: price < 1 ? 4 : 2,
    }).format(price);
  };

  const formatChange = (val: number) => {
    const sign = val >= 0 ? '+' : '';
    return `${sign}${val.toFixed(2)}`;
  };

  const formatPercent = (val: number) => {
    const sign = val >= 0 ? '+' : '';
    return `${sign}${val.toFixed(2)}%`;
  };

  const sizeClasses = {
    sm: 'text-sm',
    md: 'text-base',
    lg: 'text-lg',
    xl: 'text-2xl',
  };

  const changeSizeClasses = {
    sm: 'text-xs',
    md: 'text-sm',
    lg: 'text-base',
    xl: 'text-lg',
  };

  return (
    <div className={cn('flex items-baseline gap-2', className)}>
      <span className={cn('font-mono font-semibold', sizeClasses[size])}>
        {formatPrice(value)}
      </span>
      {showChange && change !== undefined && changePercent !== undefined && (
        <span className={cn(
          'font-mono',
          changeSizeClasses[size],
          isPositive ? 'text-success' : 'text-destructive'
        )}>
          {formatChange(change)} ({formatPercent(changePercent)})
        </span>
      )}
    </div>
  );
}

interface PercentChangeProps {
  value: number;
  size?: 'sm' | 'md' | 'lg';
  showIcon?: boolean;
  className?: string;
}

export function PercentChange({ value, size = 'md', showIcon = true, className }: PercentChangeProps) {
  const isPositive = value >= 0;
  
  const sizeClasses = {
    sm: 'text-xs',
    md: 'text-sm',
    lg: 'text-base',
  };

  return (
    <span className={cn(
      'font-mono font-medium inline-flex items-center gap-0.5',
      sizeClasses[size],
      isPositive ? 'text-success' : 'text-destructive',
      className
    )}>
      {showIcon && (
        <span className="text-[0.8em]">{isPositive ? '▲' : '▼'}</span>
      )}
      {isPositive ? '+' : ''}{value.toFixed(2)}%
    </span>
  );
}

interface CurrencyDisplayProps {
  value: number;
  currency?: string;
  compact?: boolean;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
  className?: string;
}

export function CurrencyDisplay({ value, currency = 'USD', compact, size = 'md', className }: CurrencyDisplayProps) {
  const { compactNumbers } = useAppSettings();
  // Respect an explicit override from the caller; otherwise fall back to the
  // user's global "Compact Number Format" setting.
  const useCompact = compact ?? compactNumbers;

  const formatValue = () => {
    if (useCompact) {
      if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
      if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
      if (value >= 1e3) return `$${(value / 1e3).toFixed(1)}K`;
    }
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  const sizeClasses = {
    sm: 'text-sm',
    md: 'text-base',
    lg: 'text-lg',
    xl: 'text-2xl',
    '2xl': 'text-4xl',
  };

  return (
    <span className={cn('font-mono font-semibold', sizeClasses[size], className)}>
      {formatValue()}
    </span>
  );
}
