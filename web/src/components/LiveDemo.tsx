import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2, Lock, Play, RotateCcw, ShieldCheck, Sparkles, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type SessionView, type TryRun, watchSession } from "../lib/api";
import { humanTimes, istLocal, label, LANG, sinceStart } from "../lib/format";
import { Phone, type PhoneScreen } from "./Phone";
import { type Stage, SystemDiagram } from "./SystemDiagram";
import { Badge, Button } from "./ui";

const KIND_STYLE: Record<string, { color: string; tag: string }> = {
  event: { color: "text-sig-event", tag: "EVENT" },
  detect: { color: "text-sig-detect", tag: "DETECT" },
  nudge: { color: "text-sig-nudge", tag: "NUDGE" },
  call: { color: "text-sig-call", tag: "CALL" },
  webhook: { color: "text-sig-webhook", tag: "WEBHOOK" },
  ai: { color: "text-sig-ai", tag: "AI" },
  action: { color: "text-saffron-400", tag: "ACTION" },
  skip: { color: "text-fg-faint", tag: "SKIP" },
  done: { color: "text-sig-call", tag: "DONE" },
};
const STAGE_OF: Record<string, Stage> = {
  event: "ingest", detect: "detect", nudge: "nudge", call: "call", webhook: "webhook", ai: "ai", action: "action", skip: "detect",
};
const MILESTONES = [
  { kind: "detect", label: "Detected" },
  { kind: "nudge", label: "Nudged" },
  { kind: "call", label: "Called" },
  { kind: "ai", label: "Understood" },
];

export function LiveDemo() {
  const [run, setRun] = useState<TryRun | null>(null);
  const [view, setView] = useState<SessionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    if (!run) return;
    return watchSession(run.session_id, setView);
  }, [run]);

  const finished = !!view?.finished;
  useEffect(() => {
    if (!run || finished) return;
    const t0 = Date.now();
    const t = window.setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [run, finished]);

  // Let the live captions finish playing before showing the "call ended" screen.
  useEffect(() => {
    if (!finished) return;
    const turns = view?.call?.transcript.length ?? 0;
    const t = window.setTimeout(() => setEnded(true), 1300 * turns + 1500);
    return () => window.clearTimeout(t);
  }, [finished]);

  const start = async () => {
    setStarting(true);
    setError(null);
    setView(null);
    setEnded(false);
    setElapsed(0);
    try {
      setRun(await api.startTry());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const kinds = useMemo(() => new Set(view?.timeline.map((t) => t.kind)), [view]);
  const last = view?.timeline[view.timeline.length - 1];
  const stage: Stage = !run ? "idle" : last ? STAGE_OF[last.kind] ?? "action" : "ingest";
  const nudge = view?.timeline.find((t) => t.kind === "nudge")?.detail ?? null;
  const call = view?.call;

  let screen: PhoneScreen = "idle";
  if (run) {
    if (call && call.transcript.length > 0) screen = ended ? "ended" : "in-call";
    else if (call && call.status === "in_progress") screen = "in-call";
    else if (call && ["initiating", "queued", "ringing"].includes(call.status)) screen = "ringing";
    else if (kinds.has("nudge")) screen = "nudge";
    else if (kinds.has("detect")) screen = "closed";
    else screen = "app-error";
  }
  const base = view?.timeline[0] ? new Date(view.timeline[0].at).getTime() : 0;
  const waiting = run && !finished
    ? !kinds.has("detect") ? "Recording the event…"
      : !kinds.has("nudge") ? "The dispatcher picks up due work every 10 seconds…"
      : !kinds.has("call") ? "The call is scheduled a minute after the nudge, like the real cadence…"
      : !call?.transcript.length ? "Call in progress…"
      : "Gemini is reading the conversation…"
    : null;

  return (
    <div className="card overflow-hidden">
      {/* header bar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-white/[0.06] bg-ink-900/60 px-5 py-4">
        {run ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{run.name}</span>
            <Badge>{LANG[run.language] ?? run.language}</Badge>
            <Badge tone="bad">stuck at {label(run.step)}</Badge>
          </div>
        ) : (
          <div className="text-sm text-fg-muted">A synthetic user, a real pipeline. Takes about a minute.</div>
        )}
        <div className="ml-auto flex items-center gap-3">
          {run && (
            <span className="num font-mono text-xs text-fg-faint">
              {finished ? "finished" : `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
            </span>
          )}
          <Button onClick={start} disabled={starting || (!!run && !finished)}>
            {starting ? <Loader2 size={16} className="animate-spin" /> : run && finished ? <RotateCcw size={16} /> : <Play size={16} />}
            {starting ? "Starting…" : run && !finished ? "Running" : run ? "Run another" : "Start a live run"}
          </Button>
        </div>
      </div>

      {/* milestones */}
      <div className="grid grid-cols-4 border-b border-white/[0.06]">
        {MILESTONES.map((m, i) => {
          const done = kinds.has(m.kind);
          const active = run && !done && (i === 0 || kinds.has(MILESTONES[i - 1].kind));
          return (
            <div key={m.kind} className={`flex items-center justify-center gap-2 px-2 py-3 text-xs sm:text-sm ${i ? "border-l border-white/[0.06]" : ""}`}>
              <span className={`grid h-5 w-5 place-items-center rounded-full text-[10px] transition-all ${done ? "bg-sig-call text-ink-950" : active ? "ring-1 ring-saffron-500 text-saffron-400" : "ring-1 ring-white/15 text-fg-faint"}`}>
                {done ? <Check size={12} strokeWidth={3} /> : active ? <Loader2 size={11} className="animate-spin" /> : i + 1}
              </span>
              <span className={done ? "text-fg" : "text-fg-faint"}>{m.label}</span>
            </div>
          );
        })}
      </div>

      <div className="grid gap-0 lg:grid-cols-[320px_1fr]">
        {/* the user's side */}
        <div className="border-b border-white/[0.06] bg-[radial-gradient(80%_60%_at_50%_30%,rgba(255,178,36,.06),transparent)] px-4 py-8 lg:border-b-0 lg:border-r">
          <div className="mb-4 text-center text-xs uppercase tracking-[0.16em] text-fg-faint">What {run?.name.split(" ")[0] ?? "the user"} sees</div>
          <Phone
            screen={screen}
            name={run?.name}
            step={run?.step}
            nudge={nudge}
            transcript={call?.transcript ?? []}
            callSeconds={call?.duration_seconds ?? 0}
          />
        </div>

        {/* behind the scenes */}
        <div className="min-w-0 p-5">
          <div className="mb-2 text-xs uppercase tracking-[0.16em] text-fg-faint">Behind the scenes</div>
          <SystemDiagram stage={stage} user={run?.name.split(" ")[0]} />
          <div className="mt-4 rounded-xl border border-white/[0.06] bg-ink-950/80">
            <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-2 font-mono text-[11px] text-fg-faint">
              <span className="h-2 w-2 rounded-full bg-sig-bad/70" /><span className="h-2 w-2 rounded-full bg-saffron-500/70" /><span className="h-2 w-2 rounded-full bg-sig-call/70" />
              <span className="ml-2">event log · streamed from the server</span>
            </div>
            <div className="scroll-thin h-56 overflow-y-auto px-4 py-3 font-mono text-[12px] leading-relaxed">
              {!run && <div className="text-fg-faint">$ waiting for a run…</div>}
              <AnimatePresence initial={false}>
                {view?.timeline.map((t, i) => {
                  const k = KIND_STYLE[t.kind] ?? KIND_STYLE.event;
                  return (
                    <motion.div key={`${t.at}-${i}`} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} className="flex gap-3 py-0.5">
                      <span className="num shrink-0 text-fg-faint">{sinceStart(t.at, base)}</span>
                      <span className={`w-16 shrink-0 font-semibold ${k.color}`}>{k.tag}</span>
                      <span className="min-w-0 text-fg-muted">
                        <span className="text-fg">{t.title}</span>
                        {t.detail && <span className="block truncate text-fg-faint">{humanTimes(t.detail)}</span>}
                      </span>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
              {waiting && (
                <div className="flex items-center gap-2 py-1 text-fg-faint">
                  <Loader2 size={12} className="animate-spin" /> {waiting}
                </div>
              )}
              {error && <div className="py-1 text-sig-bad">! {error}</div>}
            </div>
          </div>
        </div>
      </div>

      <AnimatePresence>{view?.analysis && <InsightPanel view={view} />}</AnimatePresence>
    </div>
  );
}

function InsightPanel({ view }: { view: SessionView }) {
  const a = view.analysis!;
  const r = a.result;
  const masked = Object.entries(a.redactions ?? {}).map(([k, n]) => `${n} ${k.toLowerCase()}`);
  const facts: [string, string][] = [
    ["Outcome", label(r.outcome)],
    ["Why they stopped", label(r.dropoff_reason)],
    ["Language spoken", LANG[r.spoken_language] ?? r.spoken_language],
    ["Call back at", istLocal(r.callback_requested_at)],
    ["Stop calling?", r.do_not_call ? "Yes: added to DND" : "No"],
    ["Confidence", `${Math.round(r.confidence * 100)}%`],
  ];
  return (
    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} className="border-t border-white/[0.06] bg-[linear-gradient(180deg,rgba(167,139,250,.06),transparent)]">
      <div className="grid gap-6 p-6 lg:grid-cols-[1.3fr_1fr]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Sparkles size={16} className="text-sig-ai" />
            <span className="font-medium">{a.source === "llm" ? "What Gemini understood" : "What the fallback rules understood"}</span>
            {a.latency_ms != null && <Badge tone="ai"><Zap size={11} /> {a.latency_ms} ms</Badge>}
          </div>
          <p className="mb-4 text-[15px] leading-relaxed text-fg">{r.summary}</p>
          {r.key_quote && (
            <blockquote className="mb-4 border-l-2 border-sig-ai/60 pl-3 text-[14px] italic text-fg-muted">
              “{r.key_quote}”
              <span className="mt-1 block not-italic text-xs text-fg-faint">Evidence, checked word-for-word against the transcript</span>
            </blockquote>
          )}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            {facts.map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-fg-faint">{k}</dt>
                <dd className="text-sm font-medium first-letter:uppercase">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="space-y-3">
          <div className="rounded-xl border border-saffron-500/25 bg-saffron-500/[0.06] p-4">
            <div className="mb-1 text-xs uppercase tracking-[0.14em] text-saffron-400">Revive acted on it</div>
            <div className="text-sm">{a.actions?.length ? humanTimes(a.actions.join("; ")) : "Nothing to do: the user finished on the call."}</div>
          </div>
          <div className="rounded-xl border border-white/[0.07] p-4 text-sm text-fg-muted">
            <div className="mb-1 flex items-center gap-1.5 text-fg"><Lock size={14} /> Privacy</div>
            {masked.length ? `Masked before anything reached the AI: ${masked.join(", ")}.` : "No personal data needed masking."}
          </div>
          <div className="rounded-xl border border-white/[0.07] p-4 text-sm text-fg-muted">
            <div className="mb-1 flex items-center gap-1.5 text-fg"><ShieldCheck size={14} /> Guardrails</div>
            Schema-validated output{r.key_quote ? ", evidence quote verified" : ""}
            {r.callback_requested_at ? ", callback moved into legal calling hours" : ""}
            {r.do_not_call ? ", stop request honoured" : ""}.
          </div>
        </div>
      </div>
    </motion.div>
  );
}
