import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import { Bell, Check, CheckCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { api } from '@/lib/api';
import { formatAlertHeadline, formatMoney } from './alertUtils';

export function AlertsBell() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  // Tracks which alert ids we've already surfaced a toast for. Starts empty
  // on purpose: alerts that triggered while the app was closed toast on
  // entry, so a fired trailing stop greets the user when they come back.
  const seenIdsRef = useRef<Set<string>>(new Set());

  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts', 'unacknowledged'],
    queryFn: () => api.getAlerts('unacknowledged'),
    refetchInterval: 60000,
  });

  useEffect(() => {
    const currentIds = new Set(alerts.map((a) => a.id));
    const newlyTriggered = alerts.filter((a) => !seenIdsRef.current.has(a.id));
    for (const alert of newlyTriggered) {
      toast(`Alert triggered: ${formatAlertHeadline(alert)}`, {
        description: alert.triggeredPrice != null ? `Hit ${formatMoney(alert.triggeredPrice)}` : undefined,
      });
    }
    seenIdsRef.current = currentIds;
  }, [alerts]);

  const acknowledgeMutation = useMutation({
    mutationFn: (id: string) => api.acknowledgeAlert(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
    onError: () => toast.error('Failed to acknowledge alert'),
  });

  const acknowledgeAllMutation = useMutation({
    mutationFn: async () => {
      await Promise.all(alerts.map((a) => api.acknowledgeAlert(a.id)));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
    onError: () => toast.error('Failed to acknowledge alerts'),
  });

  const count = alerts.length;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8 relative">
          <Bell className="h-4 w-4" />
          {count > 0 && (
            <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-destructive-foreground">
              {count > 9 ? '9+' : count}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="font-semibold text-sm">Alerts</span>
          {count > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs gap-1"
              onClick={() => acknowledgeAllMutation.mutate()}
              disabled={acknowledgeAllMutation.isPending}
            >
              <CheckCheck className="h-3.5 w-3.5" />
              Acknowledge all
            </Button>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto">
          {count === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">No triggered alerts.</div>
          ) : (
            alerts.map((alert) => (
              <div
                key={alert.id}
                className="flex items-start justify-between gap-2 px-4 py-3 border-b border-border last:border-0"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium">{formatAlertHeadline(alert)}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {alert.triggeredPrice != null && `Hit ${formatMoney(alert.triggeredPrice)} · `}
                    {alert.triggeredAt && formatDistanceToNow(alert.triggeredAt, { addSuffix: true })}
                  </div>
                  {alert.note && <div className="text-xs text-muted-foreground italic mt-0.5">{alert.note}</div>}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  onClick={() => acknowledgeMutation.mutate(alert.id)}
                  disabled={acknowledgeMutation.isPending}
                  title="Acknowledge"
                >
                  <Check className="h-4 w-4" />
                </Button>
              </div>
            ))
          )}
        </div>
        <div className="px-4 py-2.5 border-t border-border">
          <Link to="/alerts" className="text-sm text-primary hover:underline" onClick={() => setOpen(false)}>
            Manage alerts
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
