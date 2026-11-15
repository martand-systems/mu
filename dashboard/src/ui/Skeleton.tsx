// Skeleton: the shape of the content while it loads. Nothing for the first 300 ms (a fast answer never flashes),
// static blocks (no shimmer: motion is 120 ms fades only, §5.3).
import { useEffect, useState } from "react";

export function useDelayed(ms = 300): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setOn(true), ms);
    return () => clearTimeout(t);
  }, [ms]);
  return on;
}

export function SkeletonLine({ w = "100%", h = 12 }: { w?: string; h?: number }) {
  return <span className="block rounded-sm bg-surface-raised" style={{ width: w, height: h }} />;
}

export function Skeleton({ lines = 3, label }: { lines?: number; label: string }) {
  const show = useDelayed();
  return (
    <div aria-busy="true" aria-label={label} className="flex flex-col gap-2.5 py-1" data-testid="skeleton">
      {show && Array.from({ length: lines }, (_, i) => <SkeletonLine key={i} w={`${[92, 70, 84, 60, 76][i % 5]}%`} />)}
    </div>
  );
}
