import { cn } from '@/lib/utils';

interface SparklineProps {
  data?: number[];
  width?: number;
  height?: number;
  className?: string;
  positive?: boolean;
}

export function Sparkline({ 
  data = [4, 5, 6, 5, 7, 8, 7, 9, 8, 10, 9, 11],
  width = 80,
  height = 24,
  className,
  positive = true
}: SparklineProps) {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  
  const points = data.map((value, index) => {
    const x = (index / (data.length - 1)) * width;
    const y = height - ((value - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');

  const strokeColor = positive ? 'hsl(var(--success))' : 'hsl(var(--destructive))';
  const fillColor = positive ? 'hsl(var(--success) / 0.1)' : 'hsl(var(--destructive) / 0.1)';

  const areaPoints = `0,${height} ${points} ${width},${height}`;

  return (
    <svg 
      width={width} 
      height={height} 
      className={cn('overflow-visible', className)}
      viewBox={`0 0 ${width} ${height}`}
    >
      <polygon
        points={areaPoints}
        fill={fillColor}
      />
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
