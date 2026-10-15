import { motion } from "framer-motion";
import {
  ArrowRight, BookOpen, Bot, BrainCircuit, CheckCircle2, Database, FileLock2, Github,
  LayoutDashboard, PhoneCall, Radar, RefreshCcw, ShieldCheck, Workflow,
} from "lucide-react";
import { useEffect, useState } from "react";
import { GITHUB } from "../components/Chrome";
import { LiveDemo } from "../components/LiveDemo";
import { SystemDiagram, useCycle } from "../components/SystemDiagram";
import { Badge, Button, CountUp, LiveDot, Reveal, SectionHeading } from "../components/ui";
import { api, type Summary } from "../lib/api";
import { label, minutesHuman, pct } from "../lib/format";

export default function Landing() {
  const [summary, setSummary] = useState<Summary | null>(null);
  useEffect(() => {
    api.demoSummary(30).then(setSummary).catch(() => setSummary(null));
  }, []);
  return (
    <>
      <Hero />
      <Metrics s={summary} />
      <Problem s={summary} />
      <HowItWorks />
      <section id="live" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-24 sm:px-6">
        <SectionHeading eyebrow="Live demo" title={<>Don't take my word for it. <span className="text-saffron-500">Watch it run.</span></>}>
          One click creates a user who's stuck in KYC on the live server. Then the real system takes over:
          background jobs, a phone call with signed webhooks, and a real Gemini analysis, streamed to you as it happens.
        </SectionHeading>
        <Reveal><LiveDemo /></Reveal>
      </section>
      <Engineering />
      <Architecture />
      <ClosingCta />
    </>
  );
}

function Hero() {
  const stage = useCycle(true);
  return (
    <section className="relative overflow-hidden">
      <div className="grid-bg pointer-events-none absolute inset-0" />
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[900px] -translate-x-1/2 rounded-full bg-saffron-500/10 blur-[120px]" />
      <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-16 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
        <div>
          <motion.a
            href="#live"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-fg-muted transition-colors hover:border-white/20"
          >
            <LiveDot /> Live on the internet · try it in your browser <ArrowRight size={12} />
          </motion.a>
          <motion.h1
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.05, duration: 0.6 }}
            className="text-[2.6rem] font-semibold leading-[1.05] tracking-tight sm:text-6xl"
            style={{ textWrap: "balance" }}
          >
            Users quit KYC halfway. <span className="bg-gradient-to-r from-saffron-400 to-saffron-600 bg-clip-text text-transparent">Revive brings them back.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.12, duration: 0.6 }}
            className="mt-6 max-w-xl text-lg leading-relaxed text-fg-muted"
          >
            A backend for fintech apps that notices when someone gets stuck in onboarding, reaches them with a nudge and an AI voice call in
            their own language, uses Gemini to understand why they stopped, and acts on what they said.
          </motion.p>
          <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2, duration: 0.6 }} className="mt-8 flex flex-wrap gap-3">
            <Button href="#live">
              <PhoneCall size={16} /> Watch a live run
            </Button>
            <Button to="/dashboard" variant="outline">
              <LayoutDashboard size={16} /> Explore the dashboard
            </Button>
          </motion.div>
          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-fg-faint">
            <a href="/docs" className="inline-flex items-center gap-1.5 hover:text-fg"><BookOpen size={14} /> API reference</a>
            <a href={GITHUB} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 hover:text-fg"><Github size={14} /> Source code</a>
            <span>Python · FastAPI · PostgreSQL · Redis · Gemini</span>
          </div>
        </div>
        <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.15, duration: 0.7 }} className="glass rounded-3xl p-4 sm:p-5">
          <div className="mb-2 flex items-center justify-between px-1 text-xs text-fg-faint">
            <span className="font-mono">revive / pipeline</span>
            <span className="inline-flex items-center gap-1.5"><LiveDot /> running</span>
          </div>
          <SystemDiagram stage={stage} />
        </motion.div>
      </div>
    </section>
  );
}

function Metrics({ s }: { s: Summary | null }) {
  const items: [string, number, (n: number) => string, string][] = s
    ? [
        ["Users started KYC", s.sessions.started, (n) => Math.round(n).toString(), "in the demo month"],
        ["Got stuck", s.sessions.dropped_off, (n) => Math.round(n).toString(), `${pct(s.sessions.dropped_off / Math.max(1, s.sessions.started))} of them`],
        ["Brought back", s.sessions.recovered, (n) => Math.round(n).toString(), `${pct(s.sessions.recovery_rate)} recovery rate`],
        ["Calls analysed by AI", s.insights.analysed, (n) => Math.round(n).toString(), `${pct(s.calls.answer_rate)} of calls answered`],
      ]
    : [];
  return (
    <section className="border-y border-white/[0.06] bg-ink-900/50">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-px px-4 sm:px-6 lg:grid-cols-4">
        {(items.length ? items : Array.from({ length: 4 }, () => null)).map((it, i) => (
          <div key={i} className="px-2 py-8 sm:px-6">
            {it ? (
              <>
                <div className="text-4xl font-semibold tracking-tight"><CountUp value={it[1]} format={it[2]} /></div>
                <div className="mt-1 text-sm font-medium">{it[0]}</div>
                <div className="text-xs text-fg-faint">{it[3]}</div>
              </>
            ) : (
              <div className="h-[72px] animate-pulse rounded-lg bg-white/[0.04]" />
            )}
          </div>
        ))}
      </div>
      <p className="pb-4 text-center text-xs text-fg-faint">
        A simulated month of synthetic users, pushed through the real pipeline on this server
        {s?.sessions.median_minutes_to_recover != null && <> · median time to recover {minutesHuman(s.sessions.median_minutes_to_recover)}</>}
      </p>
    </section>
  );
}

function Problem({ s }: { s: Summary | null }) {
  const funnel = s?.funnel ?? [];
  const drops = funnel.slice(1).map((f, i) => ({ step: f.step, lost: funnel[i].reached - f.reached }));
  const worst = drops.reduce((a, b) => (b.lost > a.lost ? b : a), { step: "", lost: -1 });
  return (
    <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-24 sm:px-6 lg:grid-cols-2">
      <div>
        <SectionHeading eyebrow="The problem" title="Every user who abandons KYC is one you already paid for.">
          Lending and broking apps spend heavily to get someone to install and sign up. Then an OTP doesn't arrive, a PAN name doesn't
          match, the video KYC drops, and that user is gone, along with what it cost to acquire them.
        </SectionHeading>
        <Reveal className="space-y-3">
          {[
            ["Who pays for Revive", "The fintech: KreditBee, a broker, an insurer. One integration, events from their backend."],
            ["Who it helps", "Their users, stuck and unsure what to do next, get help in their language."],
            ["What it's worth", "Each recovered user is revenue the fintech would otherwise have lost."],
          ].map(([t, d]) => (
            <div key={t} className="flex gap-3">
              <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-saffron-500" />
              <div><span className="font-medium">{t}.</span> <span className="text-fg-muted">{d}</span></div>
            </div>
          ))}
        </Reveal>
      </div>
      <Reveal delay={0.1} className="card p-6">
        <div className="mb-1 flex items-center justify-between">
          <div className="text-sm font-medium">The KYC funnel, demo month</div>
          <Badge tone="bad">biggest drop: {label(worst.step) || "…"}</Badge>
        </div>
        <div className="mb-5 text-xs text-fg-faint">Share of users who reached each step</div>
        <div className="space-y-3">
          {funnel.length === 0 && Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-8 animate-pulse rounded-md bg-white/[0.04]" />)}
          {funnel.map((f, i) => {
            const hot = f.step === worst.step;
            return (
              <div key={f.step} className="grid grid-cols-[110px_1fr_48px] items-center gap-3 text-sm">
                <span className={`truncate ${hot ? "text-fg" : "text-fg-muted"}`}>{label(f.step)}</span>
                <div className="h-8 overflow-hidden rounded-md bg-white/[0.03]">
                  <motion.div
                    initial={{ width: 0 }}
                    whileInView={{ width: `${f.pct_of_started * 100}%` }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.9, delay: i * 0.08, ease: [0.21, 0.6, 0.35, 1] }}
                    className={`h-full rounded-md ${hot ? "bg-gradient-to-r from-sig-bad/80 to-sig-bad/50" : "bg-gradient-to-r from-saffron-500/80 to-saffron-500/40"}`}
                  />
                </div>
                <span className="num text-right text-fg-muted">{pct(f.pct_of_started)}</span>
              </div>
            );
          })}
        </div>
      </Reveal>
    </section>
  );
}

const STEPS = [
  { icon: Radar, title: "Detect", body: "A worker finds users idle too long on a step: 15 minutes on an OTP, 45 on video KYC. Each fintech sets its own rules." },
  { icon: PhoneCall, title: "Reach out", body: "An in-app nudge first, then an AI voice call. Only inside legal calling hours, never to DND numbers, at most twice a day." },
  { icon: BrainCircuit, title: "Understand", body: "Gemini reads the redacted transcript: why they stopped, what they want, in Hindi, Hinglish or English." },
  { icon: RefreshCcw, title: "Act & report", body: "“Call me at 6” books a 6pm call. “Don't call me” adds them to DND. The dashboard shows what's working." },
];

function HowItWorks() {
  return (
    <section className="border-y border-white/[0.06] bg-ink-900/40">
      <div className="mx-auto max-w-6xl px-4 py-24 sm:px-6">
        <SectionHeading eyebrow="How it works" title="Four steps, fully automatic." center>
          The fintech's backend sends one event per KYC step. Everything after that is Revive.
        </SectionHeading>
        <div className="relative grid gap-4 md:grid-cols-4">
          <div className="absolute left-0 right-0 top-[38px] hidden h-px bg-gradient-to-r from-transparent via-saffron-500/40 to-transparent md:block" />
          {STEPS.map((s, i) => (
            <Reveal key={s.title} delay={i * 0.08} className="card relative p-5">
              <div className="mb-4 grid h-11 w-11 place-items-center rounded-xl bg-saffron-500/10 ring-1 ring-saffron-500/30">
                <s.icon size={20} className="text-saffron-400" />
              </div>
              <div className="mb-1 font-mono text-xs text-fg-faint">0{i + 1}</div>
              <div className="mb-2 text-lg font-semibold">{s.title}</div>
              <p className="text-sm leading-relaxed text-fg-muted">{s.body}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

const PROOFS = [
  ["Row lock on the session", "18 of 20 concurrent updates lost"],
  ["Row lock in the detector", "Two workers both processed every user"],
  ["Row lock on calls", "Call stuck at “ringing” after “completed”"],
  ["Redaction before the AI", "Raw phone number in the Gemini request"],
];

function Engineering() {
  return (
    <section className="mx-auto max-w-6xl px-4 py-24 sm:px-6">
      <SectionHeading eyebrow="Engineering" title="Built like it handles real money.">
        The features are the easy part. What matters is what happens on a bad day: retries, races, crashes, a flaky provider, an
        AI that makes things up. Every claim here has a test.
      </SectionHeading>
      <div className="grid gap-4 lg:grid-cols-6">
        <Reveal className="card p-6 lg:col-span-4">
          <div className="mb-1 flex items-center gap-2 font-medium"><ShieldCheck size={18} className="text-saffron-400" /> Proven by breaking it</div>
          <p className="mb-5 text-sm text-fg-muted">I removed each safeguard and ran the tests. This is what broke:</p>
          <div className="overflow-hidden rounded-xl border border-white/[0.07]">
            {PROOFS.map(([what, broke], i) => (
              <div key={what} className={`grid grid-cols-[1fr_1.3fr] gap-4 px-4 py-3 text-sm ${i ? "border-t border-white/[0.06]" : ""}`}>
                <span className="text-fg-muted">Without: <span className="text-fg">{what}</span></span>
                <span className="text-sig-bad">{broke}</span>
              </div>
            ))}
          </div>
        </Reveal>
        <Reveal delay={0.05} className="card p-6 lg:col-span-2">
          <div className="mb-1 flex items-center gap-2 font-medium"><BrainCircuit size={18} className="text-sig-ai" /> AI, measured</div>
          <p className="mb-5 text-sm text-fg-muted">Held-out conversations the system never saw, scored on every field.</p>
          {[
            ["Gemini + guardrails", 6, "bg-sig-ai"],
            ["Keyword rules", 2, "bg-white/25"],
          ].map(([n, v, c]) => (
            <div key={n as string} className="mb-4">
              <div className="mb-1.5 flex justify-between text-sm"><span>{n}</span><span className="num text-fg-muted">{v}/6</span></div>
              <div className="h-2.5 rounded-full bg-white/[0.05]">
                <motion.div initial={{ width: 0 }} whileInView={{ width: `${((v as number) / 6) * 100}%` }} viewport={{ once: true }} transition={{ duration: 1 }} className={`h-full rounded-full ${c}`} />
              </div>
            </div>
          ))}
          <p className="text-xs text-fg-faint">Includes a prompt-injection attempt. The rules score 100% on the cases they were written for: that's overfitting, and why the model leads.</p>
        </Reveal>
        <Reveal delay={0.05} className="card p-6 lg:col-span-2">
          <div className="mb-3 flex items-center gap-2 font-medium"><FileLock2 size={18} className="text-sig-nudge" /> Personal data never leaves</div>
          <div className="space-y-2 font-mono text-[12px]">
            <div className="rounded-lg bg-sig-bad/10 px-3 py-2 text-fg-muted">mera number <span className="text-sig-bad">9812345678</span>, OTP <span className="text-sig-bad">482913</span></div>
            <div className="text-center text-fg-faint">↓ redacted before Gemini</div>
            <div className="rounded-lg bg-sig-call/10 px-3 py-2 text-fg-muted">mera number <span className="text-sig-call">[PHONE]</span>, OTP <span className="text-sig-call">[NUMBER]</span></div>
          </div>
        </Reveal>
        <Reveal delay={0.1} className="card p-6 lg:col-span-2">
          <div className="mb-3 flex items-center gap-2 font-medium"><Workflow size={18} className="text-sig-event" /> Webhooks can't be faked</div>
          <pre className="scroll-thin overflow-x-auto rounded-lg bg-ink-950 p-3 font-mono text-[11.5px] leading-relaxed text-fg-muted">
{`sig = HMAC_SHA256(secret,
        f"{timestamp}.{raw_body}")
if abs(now - timestamp) > 300:
    reject("replay")
compare_digest(sig, header)`}
          </pre>
        </Reveal>
        <Reveal delay={0.15} className="card p-6 lg:col-span-2">
          <div className="mb-3 flex items-center gap-2 font-medium"><Database size={18} className="text-saffron-400" /> Never loses, never repeats</div>
          <ul className="space-y-2 text-sm text-fg-muted">
            <li>Transactional outbox: the decision and the job commit together</li>
            <li>Leases + a reaper recover work from crashed workers</li>
            <li>Idempotency keys: a retry can't ring a phone twice</li>
            <li>Shared rate limiter, circuit breaker, rule-based fallback</li>
          </ul>
        </Reveal>
      </div>
    </section>
  );
}

const STACK = [
  ["FastAPI", "async API, typed with Pydantic"],
  ["PostgreSQL", "source of truth; row locks, SKIP LOCKED"],
  ["Redis + arq", "background jobs, shared rate limiter"],
  ["Gemini", "structured call analysis"],
  ["React + TypeScript", "this site, served by the API"],
  ["Docker + GitHub Actions", "one image, CI on every push"],
];

function Architecture() {
  return (
    <section className="border-y border-white/[0.06] bg-ink-900/40">
      <div className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-24 sm:px-6 lg:grid-cols-[1fr_1.3fr]">
        <div>
          <SectionHeading eyebrow="Architecture" title="Postgres is the source of truth. Everything else can fail.">
            Work that must not be lost is written to the database in the same transaction as the change that caused it, then claimed by
            workers with leases. Redis only carries "do job X" messages, so losing it loses nothing.
          </SectionHeading>
          <Reveal className="grid grid-cols-2 gap-3">
            {STACK.map(([n, d]) => (
              <div key={n} className="rounded-xl border border-white/[0.07] bg-ink-850 p-3">
                <div className="text-sm font-medium">{n}</div>
                <div className="text-xs text-fg-faint">{d}</div>
              </div>
            ))}
          </Reveal>
        </div>
        <Reveal delay={0.1} className="card p-5">
          <SystemDiagram stage="idle" showCaption={false} />
        </Reveal>
      </div>
    </section>
  );
}

function ClosingCta() {
  return (
    <section className="mx-auto max-w-4xl px-4 py-24 text-center sm:px-6">
      <Reveal>
        <Bot size={34} className="mx-auto mb-5 text-saffron-500" />
        <h2 className="h-section mx-auto max-w-2xl">Read the code, or poke the API.</h2>
        <p className="mx-auto mt-4 max-w-xl text-lg text-fg-muted">
          162 tests against real Postgres and Redis, CI on every push, and an API reference you can try requests from directly.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button href={GITHUB} external><Github size={16} /> View on GitHub</Button>
          <Button href="/docs" variant="outline"><BookOpen size={16} /> API reference</Button>
          <Button to="/dashboard" variant="outline"><LayoutDashboard size={16} /> Dashboard</Button>
        </div>
      </Reveal>
    </section>
  );
}
