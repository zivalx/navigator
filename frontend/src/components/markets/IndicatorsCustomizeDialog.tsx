import { useState } from 'react';
import { Settings2, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { categoryLabels, categoryOrder, indicatorRegistry } from '@/lib/indicatorTypes';

interface IndicatorsCustomizeDialogProps {
  selectedKeys: string[];
  onToggle: (key: string) => void;
  onReset: () => void;
}

export function IndicatorsCustomizeDialog({
  selectedKeys,
  onToggle,
  onReset,
}: IndicatorsCustomizeDialogProps) {
  const [open, setOpen] = useState(false);
  const selectedSet = new Set(selectedKeys);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <Settings2 className="h-4 w-4" />
          Customize
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Customize Indicators</DialogTitle>
          <DialogDescription>
            Choose which market indicators appear in the strip.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-2 max-h-[60vh] overflow-y-auto scrollbar-thin">
          {categoryOrder.map(category => {
            const items = indicatorRegistry.filter(meta => meta.category === category);
            if (items.length === 0) return null;
            return (
              <div key={category}>
                <Label className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">
                  {categoryLabels[category]}
                </Label>
                <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {items.map(meta => (
                    <div key={meta.key} className="flex items-center gap-2">
                      <Checkbox
                        id={`indicator-${meta.key}`}
                        checked={selectedSet.has(meta.key)}
                        onCheckedChange={() => onToggle(meta.key)}
                      />
                      <Label
                        htmlFor={`indicator-${meta.key}`}
                        className="text-sm font-normal cursor-pointer"
                      >
                        {meta.label}
                      </Label>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" size="sm" onClick={onReset} className="gap-1">
            <RotateCcw className="h-3 w-3" />
            Reset to defaults
          </Button>
          <Button size="sm" onClick={() => setOpen(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
