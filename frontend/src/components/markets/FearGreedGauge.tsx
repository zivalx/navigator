import { cn } from '@/lib/utils';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { IndicatorRating } from '@/lib/types';

interface FearGreedGaugeProps {
  label: string;
  value: number | null;
  rating: IndicatorRating | null;
  change: number | null;
  error?: string | null;
  className?: string;
}

// CNN Fear & Greed zone thresholds (0-100), shared by stocks + crypto variants.
const ZONES: { rating: IndicatorRating; from: number; to: number; fill: string }[] = [
  { rating: 'extreme_fear', from: 0, to: 25, fill: 'hsl(var(--destructive))' },
  {
    rating: 'fear',
    from: 25,
    to: 45,
    fill: 'color-mix(in srgb, hsl(var(--destructive)) 55%, hsl(var(--warning)) 45%)',
  },
  { rating: 'neutral', from: 45, to: 55, fill: 'hsl(var(--muted-foreground))' },
  {
    rating: 'greed',
    from: 55,
    to: 75,
    fill: 'color-mix(in srgb, hsl(var(--success)) 55%, hsl(var(--warning)) 45%)',
  },
  { rating: 'extreme_greed', from: 75, to: 100, fill: 'hsl(var(--success))' },
];

const RATING_LABELS: Record<IndicatorRating, string> = {
  extreme_fear: 'Extreme Fear',
  fear: 'Fear',
  neutral: 'Neutral',
  greed: 'Greed',
  extreme_greed: 'Extreme Greed',
};

const RATING_BADGE_CLASSES: Record<IndicatorRating, string> = {
  extreme_fear: 'bg-destructive/15 text-destructive',
  fear: 'bg-destructive/10 text-destructive',
  neutral: 'bg-muted text-muted-foreground',
  greed: 'bg-success/10 text-success',
  extreme_greed: 'bg-success/15 text-success',
};

const CX = 100;
const CY = 100;
const R_OUTER = 90;
const R_INNER = 64;
const NEEDLE_LEN = 78;

// theta sweeps 0 (value 0, left) -> 180 (value 100, right), over the top of the circle.
function thetaForValue(value: number): number {
  const clamped = Math.max(0, Math.min(100, value));
  return (clamped / 100) * 180;
}

function polar(r: number, thetaDeg: number) {
  const rad = (thetaDeg * Math.PI) / 180;
  return { x: CX - r * Math.cos(rad), y: CY - r * Math.sin(rad) };
}

// Builds a filled donut-band segment between two values as a polygon (avoids
// arc sweep-flag ambiguity entirely — just sample points along both radii).
function zoneBandPath(fromValue: number, toValue: number, steps = 20): string {
  const theta1 = thetaForValue(fromValue);
  const theta2 = thetaForValue(toValue);
  const outer: string[] = [];
  const inner: string[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = theta1 + ((theta2 - theta1) * i) / steps;
    const po = polar(R_OUTER, t);
    outer.push(`${po.x.toFixed(2)},${po.y.toFixed(2)}`);
    const pi = polar(R_INNER, t);
    inner.unshift(`${pi.x.toFixed(2)},${pi.y.toFixed(2)}`);
  }
  return `M ${outer.join(' L ')} L ${inner.join(' L ')} Z`;
}

export function FearGreedGauge({ label, value, rating, change, error, className }: FearGreedGaugeProps) {
  const hasValue = value !== null && value !== undefined;
  const needleValue = hasValue ? value! : 50;
  const needleTheta = thetaForValue(needleValue);
  const needleTip = polar(NEEDLE_LEN, needleTheta);

  const changeIsPositive = (change ?? 0) >= 0;

  return (
    <div
      className={cn(
        'rounded-xl border border-border bg-card p-4 flex flex-col items-center',
        className
      )}
    >
      <div className="flex items-center justify-between w-full mb-1">
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
        {error && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-xs text-muted-foreground/60 cursor-help underline decoration-dotted">
                unavailable
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[220px] text-xs">
              {error}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      <svg viewBox="0 0 200 112" className="w-full max-w-[220px]" role="img" aria-label={`${label} gauge`}>
        {ZONES.map(zone => (
          <path
            key={zone.rating}
            d={zoneBandPath(zone.from, zone.to)}
            fill={zone.fill}
            stroke="hsl(var(--card))"
            strokeWidth={1.5}
            opacity={hasValue ? 1 : 0.35}
          />
        ))}

        {hasValue && (
          <g>
            <line
              x1={CX}
              y1={CY}
              x2={needleTip.x}
              y2={needleTip.y}
              stroke="hsl(var(--foreground))"
              strokeWidth={3}
              strokeLinecap="round"
            />
            <circle cx={CX} cy={CY} r={6} fill="hsl(var(--foreground))" />
          </g>
        )}
      </svg>

      <div className="flex flex-col items-center gap-1 -mt-2">
        <span className="text-2xl font-bold font-mono">
          {hasValue ? Math.round(value!) : '—'}
        </span>
        {rating && hasValue ? (
          <span
            className={cn(
              'text-xs font-medium px-2 py-0.5 rounded-full',
              RATING_BADGE_CLASSES[rating]
            )}
          >
            {RATING_LABELS[rating]}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">No data</span>
        )}
        {hasValue && change !== null && change !== undefined && (
          <span
            className={cn(
              'text-xs font-mono inline-flex items-center gap-0.5',
              changeIsPositive ? 'text-success' : 'text-destructive'
            )}
          >
            <span className="text-[0.8em]">{changeIsPositive ? '▲' : '▼'}</span>
            {changeIsPositive ? '+' : ''}
            {change.toFixed(1)} vs prev
          </span>
        )}
      </div>
    </div>
  );
}
