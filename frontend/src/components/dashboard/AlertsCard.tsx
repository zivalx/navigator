import { AlertTriangle, AlertCircle, Info } from 'lucide-react';
import { usePortfolio } from '@/contexts/PortfolioContext';
import { EmptyState } from '@/components/common/EmptyState';
import { InfoTooltip } from '@/components/common/InfoTooltip';
import { cn } from '@/lib/utils';

export function AlertsCard() {
  const { alerts } = usePortfolio();

  const getAlertIcon = (type: string) => {
    switch (type) {
      case 'missing_price':
      case 'unmapped_symbol':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      case 'api_error':
        return <AlertCircle className="h-4 w-4 text-destructive" />;
      default:
        return <Info className="h-4 w-4 text-primary" />;
    }
  };

  const getAlertClass = (type: string) => {
    switch (type) {
      case 'missing_price':
      case 'unmapped_symbol':
        return 'bg-warning/5 border-warning/20';
      case 'api_error':
        return 'bg-destructive/5 border-destructive/20';
      default:
        return 'bg-primary/5 border-primary/20';
    }
  };

  if (alerts.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-5">
        <h3 className="text-lg font-semibold mb-4">
          Alerts
          <InfoTooltip text="Data quality issues — missing prices, unmapped symbols, or API errors affecting your portfolio." />
        </h3>
        <EmptyState
          icon={<Info className="h-6 w-6" />}
          title="All Good!"
          description="No issues with your portfolio data."
          className="py-4"
        />
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <h3 className="text-lg font-semibold mb-4">
        Alerts
        <InfoTooltip text="Data quality issues — missing prices, unmapped symbols, or API errors affecting your portfolio." />
        <span className="ml-2 text-sm font-normal text-muted-foreground">
          ({alerts.length})
        </span>
      </h3>
      <div className="space-y-2">
        {alerts.map((alert) => (
          <div
            key={alert.id}
            className={cn(
              'flex items-start gap-3 p-3 rounded-lg border',
              getAlertClass(alert.type)
            )}
          >
            {getAlertIcon(alert.type)}
            <div className="flex-1 min-w-0">
              <p className="text-sm">{alert.message}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
