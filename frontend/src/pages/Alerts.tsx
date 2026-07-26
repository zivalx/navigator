import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { format } from 'date-fns';
import { toast } from 'sonner';
import { Bell, MoreHorizontal, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState } from '@/components/common/EmptyState';
import { TableRowSkeleton } from '@/components/common/LoadingSkeleton';
import { CreateAlertDialog } from '@/components/alerts/CreateAlertDialog';
import { formatMoney, ruleVerb } from '@/components/alerts/alertUtils';
import { api } from '@/lib/api';
import type { PriceAlert } from '@/lib/types';

function statusBadge(alert: PriceAlert) {
  if (alert.triggeredAt) {
    return alert.acknowledgedAt ? (
      <Badge variant="secondary">Triggered</Badge>
    ) : (
      <Badge variant="destructive">Triggered</Badge>
    );
  }
  return alert.isActive ? <Badge variant="outline">Active</Badge> : <Badge variant="secondary">Inactive</Badge>;
}

const Alerts = () => {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editAlert, setEditAlert] = useState<PriceAlert | null>(null);
  const [alertToDelete, setAlertToDelete] = useState<PriceAlert | null>(null);

  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['alerts'],
    queryFn: () => api.getAlerts(),
  });

  const reactivateMutation = useMutation({
    mutationFn: (id: string) => api.updateAlert(id, { isActive: true }),
    onSuccess: (alert) => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      toast.success(`Reactivated alert for ${alert.symbol}`);
    },
    onError: () => toast.error('Failed to reactivate alert'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteAlert(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      toast.success('Alert deleted');
      setAlertToDelete(null);
    },
    onError: () => toast.error('Failed to delete alert'),
  });

  return (
    <AppLayout>
      <div className="space-y-4 animate-fade-in">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">Alerts</h1>
          <Button className="gap-1.5" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            New Alert
          </Button>
        </div>

        <div className="rounded-xl border border-border bg-card overflow-hidden">
          {isLoading ? (
            <Table>
              <TableBody>
                {Array.from({ length: 4 }).map((_, i) => (
                  <TableRowSkeleton key={i} columns={6} />
                ))}
              </TableBody>
            </Table>
          ) : alerts.length === 0 ? (
            <EmptyState
              icon={<Bell className="h-8 w-8" />}
              title="No alerts yet"
              description="Create a price alert to get notified when an asset hits a target."
              action={
                <Button className="gap-2" onClick={() => setCreateOpen(true)}>
                  <Plus className="h-4 w-4" />
                  New Alert
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableHead>Asset</TableHead>
                  <TableHead>Rule</TableHead>
                  <TableHead className="text-right">Threshold</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Note</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {alerts.map((alert) => (
                  <TableRow key={alert.id} className="hover:bg-muted/30">
                    <TableCell>
                      <span className="font-semibold">{alert.symbol}</span>
                      {alert.name && <span className="ml-1.5 text-sm text-muted-foreground">{alert.name}</span>}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">Price {ruleVerb(alert.rule)}</TableCell>
                    <TableCell className="text-right font-mono">{formatMoney(alert.threshold)}</TableCell>
                    <TableCell>{statusBadge(alert)}</TableCell>
                    <TableCell>
                      {alert.note ? (
                        <span className="text-sm text-muted-foreground truncate max-w-[160px] block">
                          {alert.note}
                        </span>
                      ) : (
                        <span className="text-muted-foreground text-sm">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {format(alert.createdAt, 'MMM d, yyyy')}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setEditAlert(alert)}>
                            <Pencil className="h-4 w-4 mr-2" /> Edit
                          </DropdownMenuItem>
                          {!alert.isActive && alert.triggeredAt && (
                            <DropdownMenuItem
                              onClick={() => reactivateMutation.mutate(alert.id)}
                              disabled={reactivateMutation.isPending}
                            >
                              <RotateCcw className="h-4 w-4 mr-2" /> Reactivate
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem className="text-destructive" onClick={() => setAlertToDelete(alert)}>
                            <Trash2 className="h-4 w-4 mr-2" /> Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </div>

      <CreateAlertDialog open={createOpen} onOpenChange={setCreateOpen} />
      <CreateAlertDialog open={!!editAlert} onOpenChange={(open) => !open && setEditAlert(null)} editAlert={editAlert} />

      <Dialog open={!!alertToDelete} onOpenChange={(open) => !open && setAlertToDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Alert</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the alert for {alertToDelete?.symbol}? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAlertToDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => alertToDelete && deleteMutation.mutate(alertToDelete.id)}
              disabled={deleteMutation.isPending}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
};

export default Alerts;
