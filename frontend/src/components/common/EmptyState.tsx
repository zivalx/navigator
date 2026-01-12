import { AlertCircle, AlertTriangle, Info, CheckCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn(
      'flex flex-col items-center justify-center p-8 text-center',
      className
    )}>
      {icon && (
        <div className="mb-4 p-3 rounded-full bg-muted text-muted-foreground">
          {icon}
        </div>
      )}
      <h3 className="text-lg font-semibold text-foreground mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-muted-foreground max-w-sm mb-4">{description}</p>
      )}
      {action}
    </div>
  );
}

interface AlertBannerProps {
  type: 'info' | 'warning' | 'error' | 'success';
  title: string;
  message?: string;
  action?: React.ReactNode;
  onDismiss?: () => void;
  className?: string;
}

export function AlertBanner({ type, title, message, action, onDismiss, className }: AlertBannerProps) {
  const config = {
    info: { icon: Info, bgClass: 'bg-primary/10 border-primary/20', textClass: 'text-primary' },
    warning: { icon: AlertTriangle, bgClass: 'bg-warning/10 border-warning/20', textClass: 'text-warning' },
    error: { icon: AlertCircle, bgClass: 'bg-destructive/10 border-destructive/20', textClass: 'text-destructive' },
    success: { icon: CheckCircle, bgClass: 'bg-success/10 border-success/20', textClass: 'text-success' },
  };

  const { icon: Icon, bgClass, textClass } = config[type];

  return (
    <div className={cn(
      'flex items-start gap-3 p-4 rounded-lg border',
      bgClass,
      className
    )}>
      <Icon className={cn('h-5 w-5 flex-shrink-0 mt-0.5', textClass)} />
      <div className="flex-1 min-w-0">
        <p className={cn('font-medium text-sm', textClass)}>{title}</p>
        {message && (
          <p className="text-sm text-muted-foreground mt-1">{message}</p>
        )}
      </div>
      {action}
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          ×
        </button>
      )}
    </div>
  );
}

export function DemoBanner() {
  return (
    <AlertBanner
      type="warning"
      title="Demo Mode"
      message="Showing simulated prices. Connect a data provider for live quotes."
    />
  );
}
