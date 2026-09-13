import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
  tone = "default",
}: {
  title?: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: "default" | "alert" | "warn" | "good";
}) {
  const toneRing = {
    default: "border-ink-700",
    alert: "border-red-500/40",
    warn: "border-yellow-500/35",
    good: "border-accent-500/35",
  }[tone];
  return (
    <section
      className={`rise rounded-2xl border ${toneRing} bg-ink-900/70 backdrop-blur-sm shadow-xl shadow-black/30 ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 border-b border-ink-700/70 px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function Chip({
  children,
  className = "",
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${className}`}
    >
      {children}
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wider text-ink-400">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-medium text-ink-100" title={hint}>
        {value}
      </dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink-400">{children}</p>;
}

/** A labelled horizontal range bar used for prices. */
export function RangeBar({
  low,
  high,
  mid,
  outerLow,
  outerHigh,
  format,
}: {
  low: number;
  high: number;
  mid: number;
  outerLow: number;
  outerHigh: number;
  format: (v: number) => string;
}) {
  const span = Math.max(1, outerHigh - outerLow);
  const pct = (v: number) => Math.max(0, Math.min(100, ((v - outerLow) / span) * 100));
  return (
    <div className="w-full">
      <div className="relative h-2.5 w-full rounded-full bg-ink-700/70">
        <div
          className="absolute h-2.5 rounded-full bg-gradient-to-r from-accent-500/70 to-accent-400"
          style={{ left: `${pct(low)}%`, width: `${Math.max(2, pct(high) - pct(low))}%` }}
        />
        <div
          className="absolute -top-1 h-4.5 w-0.5 rounded bg-white"
          style={{ left: `${pct(mid)}%`, height: "1.05rem" }}
        />
      </div>
      <div className="mt-1.5 flex justify-between text-[10px] tabular-nums text-ink-400">
        <span>{format(outerLow)}</span>
        <span>{format(outerHigh)}</span>
      </div>
    </div>
  );
}
