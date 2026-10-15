import { motion } from "framer-motion";
import { useMemo, useState } from "react";
import { label, pct } from "../lib/format";

const BLUE = "#58a6ff";
const SAFFRON = "#ffb224";

type Day = { date: string; started: number; dropped: number; recovered: number };

/** Daily drop-offs vs recoveries: two areas on one scale, a crosshair and a tooltip. */
export function TrendChart({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 720;
  const H = 220;
  const P = { l: 34, r: 30, t: 12, b: 26 }; // right padding leaves room for the last date
  const max = Math.max(4, ...days.map((d) => Math.max(d.dropped, d.recovered)));
  const top = Math.ceil(max / 4) * 4; // round the axis up to a multiple of 4: clean tick values
  const x = (i: number) => P.l + (i * (W - P.l - P.r)) / Math.max(1, days.length - 1);
  const y = (v: number) => P.t + (1 - v / top) * (H - P.t - P.b);
  const path = (key: "dropped" | "recovered") => days.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d[key])}`).join(" ");
  const area = (key: "dropped" | "recovered") => `${path(key)} L${x(days.length - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const ticks = [0, top / 4, top / 2, (3 * top) / 4, top];
  const fmtDay = (s: string) => new Date(`${s}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="gDrop" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={BLUE} stopOpacity=".28" /><stop offset="1" stopColor={BLUE} stopOpacity="0" /></linearGradient>
          <linearGradient id="gRec" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={SAFFRON} stopOpacity=".32" /><stop offset="1" stopColor={SAFFRON} stopOpacity="0" /></linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,.06)" />
            <text x={P.l - 8} y={y(t) + 4} textAnchor="end" className="fill-fg-faint text-[10px]">{t}</text>
          </g>
        ))}
        {days.map((d, i) => (i % Math.ceil(days.length / 6) === 0 || i === days.length - 1) && (
          <text key={d.date} x={x(i)} y={H - 6} textAnchor="middle" className="fill-fg-faint text-[10px]">{fmtDay(d.date)}</text>
        ))}
        <path d={area("dropped")} fill="url(#gDrop)" />
        <path d={area("recovered")} fill="url(#gRec)" />
        <motion.path d={path("dropped")} fill="none" stroke={BLUE} strokeWidth="2" initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.2 }} />
        <motion.path d={path("recovered")} fill="none" stroke={SAFFRON} strokeWidth="2" initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.2, delay: 0.2 }} />
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={P.t} y2={y(0)} stroke="rgba(255,255,255,.25)" strokeDasharray="3 3" />
            <circle cx={x(hover)} cy={y(days[hover].dropped)} r="4" fill={BLUE} stroke="#0d1117" strokeWidth="2" />
            <circle cx={x(hover)} cy={y(days[hover].recovered)} r="4" fill={SAFFRON} stroke="#0d1117" strokeWidth="2" />
          </g>
        )}
        {days.map((_, i) => (
          <rect key={i} x={x(i) - (W - P.l - P.r) / days.length / 2} y={P.t} width={(W - P.l - P.r) / days.length} height={H - P.t - P.b} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-2 rounded-lg border border-white/10 bg-ink-800/95 px-3 py-2 text-xs shadow-xl backdrop-blur"
          style={{ left: `${Math.min(80, Math.max(2, (x(hover) / W) * 100 - 8))}%` }}
        >
          <div className="mb-1 font-medium">{fmtDay(days[hover].date)}</div>
          <div className="flex items-center gap-2"><span className="h-0.5 w-3 rounded" style={{ background: BLUE }} /><b className="num">{days[hover].dropped}</b> got stuck</div>
          <div className="flex items-center gap-2"><span className="h-0.5 w-3 rounded" style={{ background: SAFFRON }} /><b className="num">{days[hover].recovered}</b> recovered</div>
          <div className="mt-1 text-fg-faint">{days[hover].started} started</div>
        </div>
      )}
    </div>
  );
}

export function Legend({ items }: { items: [string, string][] }) {
  return (
    <div className="flex flex-wrap gap-4 text-xs text-fg-muted">
      {items.map(([name, color]) => (
        <span key={name} className="inline-flex items-center gap-1.5"><span className="h-0.5 w-3.5 rounded" style={{ background: color }} />{name}</span>
      ))}
    </div>
  );
}

/** Horizontal bars with labels and values, largest first. */
export function HBars({ rows, color = SAFFRON, valueFmt }: { rows: { key: string; count: number }[]; color?: string; valueFmt?: (n: number, total: number) => string }) {
  const total = rows.reduce((a, r) => a + r.count, 0) || 1;
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <Empty />;
  return (
    <div className="space-y-2.5">
      {rows.map((r, i) => (
        <div key={r.key} className="group grid grid-cols-[minmax(0,140px)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate text-fg-muted group-hover:text-fg first-letter:uppercase" title={label(r.key)}>{label(r.key)}</span>
          <div className="h-2 rounded-full bg-white/[0.04]">
            <motion.div initial={{ width: 0 }} whileInView={{ width: `${(r.count / max) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.8, delay: i * 0.05 }} className="h-full rounded-full" style={{ background: color }} />
          </div>
          <span className="num w-20 text-right text-xs text-fg-muted">{valueFmt ? valueFmt(r.count, total) : `${r.count} · ${pct(r.count / total)}`}</span>
        </div>
      ))}
    </div>
  );
}

/** Two values per row (dropped vs recovered), sharing one scale. */
export function PairBars({ rows }: { rows: { step: string; dropped: number; recovered: number; recovery_rate: number }[] }) {
  const max = useMemo(() => Math.max(1, ...rows.map((r) => r.dropped)), [rows]);
  if (!rows.length) return <Empty />;
  return (
    <div className="space-y-4">
      {rows.map((r, i) => (
        <div key={r.step} className="text-sm">
          <div className="mb-1.5 flex justify-between">
            <span className="text-fg-muted first-letter:uppercase">{label(r.step)}</span>
            <span className="num text-xs text-fg-faint">{r.recovered}/{r.dropped} back · <span className="text-saffron-400">{pct(r.recovery_rate)}</span></span>
          </div>
          <div className="space-y-1">
            <div className="h-2 rounded-full bg-white/[0.04]"><motion.div initial={{ width: 0 }} whileInView={{ width: `${(r.dropped / max) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.8, delay: i * 0.05 }} className="h-full rounded-full" style={{ background: BLUE }} /></div>
            <div className="h-2 rounded-full bg-white/[0.04]"><motion.div initial={{ width: 0 }} whileInView={{ width: `${(r.recovered / max) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.8, delay: i * 0.05 + 0.1 }} className="h-full rounded-full" style={{ background: SAFFRON }} /></div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function Empty({ text = "No data in this period yet." }: { text?: string }) {
  return <div className="grid h-24 place-items-center rounded-xl border border-dashed border-white/10 text-sm text-fg-faint">{text}</div>;
}

export const COLORS = { blue: BLUE, saffron: SAFFRON };
