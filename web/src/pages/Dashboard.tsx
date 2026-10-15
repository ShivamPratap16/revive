import { AnimatePresence, motion } from "framer-motion";
import { Bot, Clock, KeyRound, PhoneCall, RotateCcw, Sparkles, TrendingDown, Users, X } from "lucide-react";
import { useEffect, useState } from "react";
import { COLORS, Empty, HBars, Legend, PairBars, TrendChart } from "../components/charts";
import { Badge } from "../components/ui";
import { api, type CallDetail, type CallRow, type Summary } from "../lib/api";
import { humanTimes, istLocal, istTime, label, LANG, minutesHuman, pct, timeAgo } from "../lib/format";

const KEY_STORE = "revive_key";

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [key, setKey] = useState<string>(() => {
    try { return localStorage.getItem(KEY_STORE) ?? ""; } catch { return ""; }
  });
  const [keyOpen, setKeyOpen] = useState(false);
  const [s, setS] = useState<Summary | null>(null);
  const [calls, setCalls] = useState<CallRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openCall, setOpenCall] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const req = key ? api.tenantSummary(key, days) : api.demoSummary(days);
    req.then(setS).catch((e) => setError(e.message));
  }, [key, days]);
  useEffect(() => {
    if (!key) api.calls(30).then(setCalls).catch(() => setCalls([]));
  }, [key]);

  const saveKey = (k: string) => {
    setKey(k);
    try { k ? localStorage.setItem(KEY_STORE, k) : localStorage.removeItem(KEY_STORE); } catch { /* private mode */ }
    setKeyOpen(false);
  };

  return (
    <div className="mx-auto max-w-6xl px-4 pb-10 pt-8 sm:px-6">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow mb-2">{key ? "Your tenant" : "Public demo tenant"}</div>
          <h1 className="text-3xl font-semibold tracking-tight">KYC recovery</h1>
          <p className="mt-1 text-sm text-fg-muted">
            {key ? "Numbers for the tenant whose key you entered." : "A simulated month of users run through the real pipeline, plus every live demo run."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl border border-white/10 bg-ink-850 p-1 text-sm">
            {[7, 30, 90].map((d) => (
              <button key={d} type="button" onClick={() => setDays(d)} className={`rounded-lg px-3 py-1.5 transition-colors ${d === days ? "bg-white/10 text-fg" : "text-fg-muted hover:text-fg"}`}>
                {d}d
              </button>
            ))}
          </div>
          <div className="relative">
            <button type="button" onClick={() => setKeyOpen((o) => !o)} className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-ink-850 px-3 py-2 text-sm text-fg-muted hover:text-fg">
              <KeyRound size={14} /> {key ? "Key set" : "Your key"}
            </button>
            {keyOpen && <KeyPopover current={key} onSave={saveKey} />}
          </div>
        </div>
      </div>

      {error && <div className="mb-6 rounded-xl border border-sig-bad/30 bg-sig-bad/10 px-4 py-3 text-sm text-sig-bad">{error}</div>}

      <Kpis s={s} />

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Drop-offs and recoveries, by day" className="lg:col-span-2" right={<Legend items={[["Got stuck", COLORS.blue], ["Recovered", COLORS.saffron]]} />}>
          {s ? <TrendChart days={s.daily} /> : <Skeleton h="h-56" />}
        </Panel>
        <Panel title="Recoveries credited to">
          {s ? (
            <div className="space-y-5">
              <HBars rows={s.recovered_by_channel.map((c) => ({ key: c.key === "in_app" ? "in-app nudge" : "voice call", count: c.count }))} />
              <div className="grid grid-cols-2 gap-3 text-sm">
                <MiniStat label="Calls answered" value={pct(s.calls.answer_rate)} />
                <MiniStat label="Avg call" value={s.calls.avg_duration_seconds ? `${Math.round(s.calls.avg_duration_seconds)} s` : "—"} />
                <MiniStat label="Callbacks booked" value={String(s.insights.callbacks_requested)} />
                <MiniStat label="Asked not to call" value={String(s.insights.do_not_call_requests)} />
              </div>
            </div>
          ) : <Skeleton h="h-56" />}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Funnel" subtitle="Share of users reaching each step">
          {s ? <HBars rows={s.funnel.map((f) => ({ key: f.step, count: f.reached }))} color={COLORS.blue} valueFmt={(n) => pct(n / Math.max(1, s.sessions.started))} /> : <Skeleton />}
        </Panel>
        <Panel title="Where they get stuck" subtitle="Dropped (blue) vs brought back (saffron)">
          {s ? <PairBars rows={s.dropoffs_by_step} /> : <Skeleton />}
        </Panel>
        <Panel title="Why they stopped" subtitle="From AI analysis of answered calls">
          {s ? <HBars rows={s.insights.top_reasons} color="#a78bfa" /> : <Skeleton />}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Recovery by language">
          {s ? (
            s.by_language.length ? (
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs text-fg-faint"><th className="pb-2 font-medium">Language</th><th className="pb-2 text-right font-medium">Stuck</th><th className="pb-2 text-right font-medium">Back</th><th className="pb-2 text-right font-medium">Rate</th></tr></thead>
                <tbody className="num">
                  {s.by_language.map((l) => (
                    <tr key={l.language} className="border-t border-white/[0.05]">
                      <td className="py-2">{LANG[l.language] ?? l.language}</td>
                      <td className="py-2 text-right text-fg-muted">{l.dropped}</td>
                      <td className="py-2 text-right text-fg-muted">{l.recovered}</td>
                      <td className="py-2 text-right text-saffron-400">{pct(l.recovery_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty />
          ) : <Skeleton />}
        </Panel>
        <Panel title="Call outcomes">
          {s ? <HBars rows={s.calls.by_status} color={COLORS.blue} /> : <Skeleton />}
        </Panel>
        <Panel title="AI analysis">
          {s ? (
            <div className="space-y-4">
              <HBars rows={s.insights.by_source.map((b) => ({ key: b.key === "llm" ? "Gemini" : "rules (fallback)", count: b.count }))} color="#a78bfa" />
              <div className="grid grid-cols-2 gap-3 text-sm">
                <MiniStat label="Calls analysed" value={String(s.insights.analysed)} />
                <MiniStat label="Avg Gemini latency" value={s.insights.avg_llm_latency_ms ? `${s.insights.avg_llm_latency_ms} ms` : "—"} />
              </div>
            </div>
          ) : <Skeleton />}
        </Panel>
      </div>

      {!key && (
        <Panel title="Recent calls" subtitle="Click a call to read the conversation and what the AI made of it" className="mt-4" right={<Badge tone="ai"><Sparkles size={11} /> transcripts + analysis</Badge>}>
          {calls === null ? <Skeleton h="h-64" /> : calls.length === 0 ? <Empty text="No analysed calls yet." /> : (
            <div className="scroll-thin -mx-5 overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="border-b border-white/[0.06] text-left text-xs text-fg-faint">
                    {["User", "Stuck at", "Outcome", "Why", "Language", "When"].map((h) => <th key={h} className="px-5 pb-2 font-medium">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {calls.map((c) => (
                    <tr key={c.id} onClick={() => setOpenCall(c.id)} className="cursor-pointer border-b border-white/[0.04] transition-colors hover:bg-white/[0.03]">
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 font-medium">{c.name}{c.recovered && <Badge tone="good">recovered</Badge>}</div>
                      </td>
                      <td className="px-5 py-3 text-fg-muted first-letter:uppercase">{label(c.step)}</td>
                      <td className="px-5 py-3"><OutcomeBadge outcome={c.outcome} dnc={!!c.do_not_call} /></td>
                      <td className="px-5 py-3 text-fg-muted first-letter:uppercase">{label(c.reason)}</td>
                      <td className="px-5 py-3 text-fg-muted">{LANG[c.language] ?? c.language}</td>
                      <td className="num whitespace-nowrap px-5 py-3 text-fg-faint">{timeAgo(c.placed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      <AnimatePresence>{openCall && <CallDrawer id={openCall} onClose={() => setOpenCall(null)} />}</AnimatePresence>
    </div>
  );
}

function Kpis({ s }: { s: Summary | null }) {
  const items = s ? [
    { icon: Users, label: "Started KYC", value: String(s.sessions.started), sub: `${s.sessions.completed} completed` },
    { icon: TrendingDown, label: "Got stuck", value: String(s.sessions.dropped_off), sub: `${pct(s.sessions.dropped_off / Math.max(1, s.sessions.started))} of starts` },
    { icon: RotateCcw, label: "Brought back", value: String(s.sessions.recovered), sub: `${pct(s.sessions.recovery_rate)} recovery rate`, accent: true },
    { icon: Clock, label: "Median time to recover", value: minutesHuman(s.sessions.median_minutes_to_recover), sub: "stuck → completed" },
    { icon: PhoneCall, label: "Voice calls", value: String(s.calls.placed), sub: `${pct(s.calls.answer_rate)} answered` },
  ] : [];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {(items.length ? items : Array.from({ length: 5 }, () => null)).map((k, i) => (
        <div key={i} className={`card p-4 ${k?.accent ? "ring-1 ring-saffron-500/30" : ""}`}>
          {k ? (
            <>
              <div className="mb-3 flex items-center gap-2 text-xs text-fg-muted"><k.icon size={14} className={k.accent ? "text-saffron-400" : ""} /> {k.label}</div>
              <div className={`num text-3xl font-semibold tracking-tight ${k.accent ? "text-saffron-400" : ""}`}>{k.value}</div>
              <div className="mt-1 text-xs text-fg-faint">{k.sub}</div>
            </>
          ) : <div className="h-[84px] animate-pulse rounded-lg bg-white/[0.04]" />}
        </div>
      ))}
    </div>
  );
}

function Panel({ title, subtitle, right, children, className = "" }: { title: string; subtitle?: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`card p-5 ${className}`}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium">{title}</h2>
          {subtitle && <p className="text-xs text-fg-faint">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

const MiniStat = ({ label: l, value }: { label: string; value: string }) => (
  <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-xs text-fg-faint">{l}</div><div className="num text-lg font-semibold">{value}</div></div>
);
const Skeleton = ({ h = "h-40" }: { h?: string }) => <div className={`${h} animate-pulse rounded-xl bg-white/[0.04]`} />;

function OutcomeBadge({ outcome, dnc }: { outcome: string | null; dnc: boolean }) {
  if (dnc) return <Badge tone="bad">stop calling</Badge>;
  const tone = outcome === "completed_step_on_call" ? "good" : outcome === "callback_requested" ? "accent" : outcome === "not_interested" ? "bad" : "neutral";
  return <Badge tone={tone}>{label(outcome)}</Badge>;
}

function KeyPopover({ current, onSave }: { current: string; onSave: (k: string) => void }) {
  const [v, setV] = useState(current);
  return (
    <div className="absolute right-0 top-12 z-30 w-80 rounded-2xl border border-white/10 bg-ink-800 p-4 shadow-2xl">
      <div className="mb-1 text-sm font-medium">Use your own tenant</div>
      <p className="mb-3 text-xs text-fg-faint">Paste an API key to see that tenant's numbers. It stays in this browser.</p>
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder="rv_live_…" className="w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 font-mono text-xs outline-none focus:border-saffron-500" />
      <div className="mt-3 flex justify-between">
        <button type="button" className="text-xs text-fg-muted hover:text-fg" onClick={() => onSave("")}>Back to demo</button>
        <button type="button" className="rounded-lg bg-saffron-500 px-3 py-1.5 text-xs font-medium text-ink-950" onClick={() => onSave(v.trim())}>Load</button>
      </div>
    </div>
  );
}

function CallDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [c, setC] = useState<CallDetail | null>(null);
  useEffect(() => {
    api.call(id).then(setC).catch(() => setC(null));
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [id, onClose]);
  const r = c?.analysis?.result;
  return (
    <>
      <motion.div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
      <motion.aside
        className="scroll-thin fixed bottom-0 right-0 top-0 z-50 w-full max-w-lg overflow-y-auto border-l border-white/10 bg-ink-900 shadow-2xl"
        initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", stiffness: 320, damping: 34 }}
        role="dialog" aria-label="Call details"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-white/[0.06] bg-ink-900/90 px-5 py-4 backdrop-blur">
          <div>
            <div className="font-medium">{c?.name ?? "Loading…"}</div>
            {c && <div className="text-xs text-fg-faint">{c.phone} · stuck at {label(c.step)} · {istTime(c.placed_at)}</div>}
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-fg-muted hover:bg-white/5 hover:text-fg" aria-label="Close"><X size={18} /></button>
        </div>
        {c && (
          <div className="space-y-6 p-5">
            {r && (
              <div className="rounded-2xl border border-sig-ai/25 bg-sig-ai/[0.06] p-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium"><Sparkles size={15} className="text-sig-ai" /> {c.analysis!.source === "llm" ? "Gemini's reading" : "Fallback rules"}
                  {c.analysis!.latency_ms != null && <span className="text-xs font-normal text-fg-faint">· {c.analysis!.latency_ms} ms</span>}
                </div>
                <p className="mb-3 text-sm leading-relaxed">{r.summary}</p>
                <dl className="grid grid-cols-2 gap-3 text-sm">
                  {[["Outcome", label(r.outcome)], ["Reason", label(r.dropoff_reason)], ["Callback", istLocal(r.callback_requested_at)], ["Stop calling", r.do_not_call ? "yes" : "no"]].map(([k, v]) => (
                    <div key={k}><dt className="text-xs text-fg-faint">{k}</dt><dd className="first-letter:uppercase">{v}</dd></div>
                  ))}
                </dl>
                {c.analysis!.actions?.length ? <div className="mt-3 rounded-lg bg-saffron-500/10 px-3 py-2 text-sm text-saffron-300">{humanTimes(c.analysis!.actions.join("; "))}</div> : null}
              </div>
            )}
            <div>
              <div className="mb-3 text-xs uppercase tracking-[0.14em] text-fg-faint">Conversation</div>
              <div className="space-y-2">
                {c.transcript.map((t, i) => (
                  <div key={i} className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${t.speaker === "agent" ? "rounded-tl-md bg-white/[0.06]" : "ml-auto rounded-tr-md bg-saffron-500/90 text-ink-950"}`}>
                    <div className={`mb-0.5 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider ${t.speaker === "agent" ? "text-sig-call" : "text-ink-950/60"}`}>
                      {t.speaker === "agent" && <Bot size={11} />}{t.speaker === "agent" ? "AI agent" : c.name.split(" ")[0]}
                    </div>
                    {t.text}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-3 text-xs uppercase tracking-[0.14em] text-fg-faint">Signed webhooks received</div>
              <ol className="space-y-2 border-l border-white/10 pl-4 text-sm">
                {c.webhooks.map((w, i) => (
                  <li key={i}><span className="font-mono text-xs text-sig-event">{w.type}</span><div className="text-xs text-fg-faint">{w.effect}</div></li>
                ))}
              </ol>
            </div>
          </div>
        )}
      </motion.aside>
    </>
  );
}
