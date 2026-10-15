import { AnimatePresence, motion } from "framer-motion";
import { AlertCircle, Bot, Check, Phone as PhoneIcon, PhoneOff, Wifi } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Turn } from "../lib/api";

export type PhoneScreen = "idle" | "app-error" | "closed" | "nudge" | "ringing" | "in-call" | "ended";

const STEPS = ["Sign up", "PAN", "Aadhaar OTP", "Video KYC", "Bank"];
const STEP_INDEX: Record<string, number> = { signup: 0, pan_verification: 1, aadhaar_otp: 2, video_kyc: 3, bank_linking: 4 };
const STEP_TITLE: Record<string, string> = {
  signup: "Create your account",
  pan_verification: "Verify your PAN",
  aadhaar_otp: "Verify with Aadhaar OTP",
  video_kyc: "Video KYC",
  bank_linking: "Link your bank account",
};
const ERROR_TEXT: Record<string, string> = {
  aadhaar_otp: "We couldn't verify your OTP. Didn't receive it?",
  pan_verification: "The name on your PAN doesn't match.",
  video_kyc: "Your video KYC call dropped.",
  bank_linking: "We couldn't verify your bank account.",
  signup: "Something went wrong.",
};

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

/** Reveals transcript turns one at a time, like live captions. */
function useTyped(turns: Turn[], active: boolean) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
  }, [turns.length === 0]);
  useEffect(() => {
    if (!active || n >= turns.length) return;
    const t = window.setTimeout(() => setN((x) => x + 1), n === 0 ? 400 : 1300);
    return () => window.clearTimeout(t);
  }, [active, n, turns.length]);
  return turns.slice(0, active ? n : turns.length);
}

export function Phone({
  screen,
  name = "Ravi",
  step = "aadhaar_otp",
  nudge,
  transcript = [],
  callSeconds = 0,
}: {
  screen: PhoneScreen;
  name?: string;
  step?: string;
  nudge?: string | null;
  transcript?: Turn[];
  callSeconds?: number;
}) {
  const now = useClock();
  const first = name.split(" ")[0];
  const time = now.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const date = now.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" });
  const shown = useTyped(transcript, screen === "in-call");
  const captions = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Braces matter: newer browsers make scrollTo() return a Promise, and an effect must
    // return nothing or a cleanup function. Returning the Promise crashed React.
    captions.current?.scrollTo({ top: 1e6, behavior: "smooth" });
  }, [shown.length]);
  const lock = screen === "closed" || screen === "nudge" || screen === "idle";

  return (
    <div className="relative mx-auto w-[272px] select-none">
      <div className="rounded-[46px] border border-white/10 bg-gradient-to-b from-ink-700 to-ink-800 p-[10px] shadow-[0_40px_80px_-30px_rgba(0,0,0,.9),0_0_0_1px_rgba(255,255,255,.04)_inset]">
        <div className="relative h-[540px] overflow-hidden rounded-[38px] bg-ink-950">
          {/* status bar + island */}
          <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between px-7 pt-3 text-[11px] font-medium text-fg">
            <span className="num">{time}</span>
            <span className="absolute left-1/2 top-2.5 h-[22px] w-[86px] -translate-x-1/2 rounded-full bg-black" />
            <span className="flex items-center gap-1"><Wifi size={11} /> 5G</span>
          </div>

          <AnimatePresence mode="wait">
            {lock && (
              <motion.div key="lock" className="absolute inset-0" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="absolute inset-0 bg-[radial-gradient(120%_70%_at_50%_0%,#1b2a3f_0%,#0a0d12_60%)]" />
                <div className="relative pt-16 text-center">
                  <div className="text-[13px] text-fg-muted">{date}</div>
                  <div className="num text-[64px] font-semibold leading-none tracking-tight">{time}</div>
                </div>
                <AnimatePresence>
                  {screen === "nudge" && nudge && (
                    <motion.div
                      initial={{ y: -40, opacity: 0, scale: 0.96 }}
                      animate={{ y: 0, opacity: 1, scale: 1 }}
                      transition={{ type: "spring", stiffness: 260, damping: 22 }}
                      className="relative mx-3 mt-8 rounded-2xl border border-white/10 bg-white/[0.08] p-3 backdrop-blur-xl"
                    >
                      <div className="mb-1 flex items-center gap-2 text-[11px] text-fg-muted">
                        <span className="grid h-5 w-5 place-items-center rounded-md bg-saffron-500 text-[10px] font-bold text-ink-950">K</span>
                        KREDITBEE <span className="ml-auto">now</span>
                      </div>
                      <div className="text-[13px] leading-snug text-fg">{nudge}</div>
                    </motion.div>
                  )}
                </AnimatePresence>
                {screen === "closed" && (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }} className="relative mt-10 text-center text-[12px] text-fg-faint">
                    {first} closed the app.
                    <br />
                    Revive is deciding what to do…
                  </motion.div>
                )}
                {screen === "idle" && (
                  <div className="relative mt-10 px-6 text-center text-[12px] leading-relaxed text-fg-faint">
                    Start the demo to see what a stuck user experiences.
                  </div>
                )}
              </motion.div>
            )}

            {screen === "app-error" && (
              <motion.div key="app" className="absolute inset-0 bg-[#f7f8fa] text-[#0b1220]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="flex items-center gap-2 bg-[#0b3d91] px-5 pb-3 pt-11 text-white">
                  <span className="grid h-6 w-6 place-items-center rounded-md bg-saffron-500 text-[11px] font-bold text-ink-950">K</span>
                  <span className="text-[14px] font-semibold">KreditBee</span>
                  <span className="ml-auto text-[11px] opacity-80">Instant loan</span>
                </div>
                <div className="px-5 pt-4">
                  <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-[#5b6577]">Complete your KYC</div>
                  <div className="mb-4 flex gap-1">
                    {STEPS.map((s, i) => (
                      <div key={s} className={`h-1.5 flex-1 rounded-full ${i < (STEP_INDEX[step] ?? 2) ? "bg-[#1a7f37]" : i === (STEP_INDEX[step] ?? 2) ? "bg-[#d1242f]" : "bg-[#d8dee8]"}`} />
                    ))}
                  </div>
                  <div className="text-[18px] font-semibold">{STEP_TITLE[step] ?? "Complete your KYC"}</div>
                  <div className="mt-4 rounded-xl border border-[#f5c2c7] bg-[#fff1f2] p-3 text-[13px] text-[#a1121b]">
                    <div className="flex items-start gap-2">
                      <AlertCircle size={16} className="mt-0.5 shrink-0" />
                      {ERROR_TEXT[step] ?? "Something went wrong."}
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-6 gap-1.5">
                    {Array.from({ length: 6 }).map((_, i) => (
                      <div key={i} className="h-10 rounded-lg border border-[#d8dee8] bg-white" />
                    ))}
                  </div>
                  <div className="mt-5 rounded-xl bg-[#0b3d91] py-3 text-center text-[14px] font-semibold text-white opacity-50">Verify</div>
                </div>
              </motion.div>
            )}

            {screen === "ringing" && (
              <motion.div key="ring" className="absolute inset-0 flex flex-col items-center bg-[radial-gradient(100%_60%_at_50%_20%,#163322_0%,#07090c_70%)] pt-24" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="relative">
                  <span className="absolute inset-0 animate-ping rounded-full bg-sig-call/30" />
                  <div className="relative grid h-20 w-20 place-items-center rounded-full bg-sig-call/15 ring-1 ring-sig-call/40">
                    <Bot size={34} className="text-sig-call" />
                  </div>
                </div>
                <div className="mt-5 text-[20px] font-semibold">KreditBee Assistant</div>
                <div className="mt-1 text-[12px] text-fg-muted">AI voice agent · calling {first}…</div>
                <div className="mt-auto mb-16 flex w-full justify-around px-10">
                  <div className="grid h-14 w-14 place-items-center rounded-full bg-sig-bad"><PhoneOff size={22} /></div>
                  <motion.div animate={{ y: [0, -4, 0] }} transition={{ repeat: Infinity, duration: 0.9 }} className="grid h-14 w-14 place-items-center rounded-full bg-sig-call">
                    <PhoneIcon size={22} />
                  </motion.div>
                </div>
              </motion.div>
            )}

            {(screen === "in-call" || screen === "ended") && (
              <motion.div key="call" className="absolute inset-0 flex flex-col bg-ink-950 pt-11" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="flex flex-col items-center border-b border-white/[0.06] pb-3">
                  <div className="grid h-11 w-11 place-items-center rounded-full bg-sig-call/15 ring-1 ring-sig-call/40"><Bot size={20} className="text-sig-call" /></div>
                  <div className="mt-1.5 text-[14px] font-semibold">KreditBee Assistant</div>
                  <div className="num text-[11px] text-fg-muted">
                    {screen === "ended" ? (
                      <span className="inline-flex items-center gap-1 text-sig-call"><Check size={11} /> Call ended · {fmtSecs(callSeconds)}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sig-call" /> Live captions</span>
                    )}
                  </div>
                </div>
                <div ref={captions} className="scroll-thin flex-1 space-y-2 overflow-y-auto px-3 py-3">
                  {transcript.length === 0 && <div className="pt-10 text-center text-[12px] text-fg-faint">Connecting…</div>}
                  <AnimatePresence initial={false}>
                    {(screen === "in-call" ? shown : transcript).map((t, i) => (
                      <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        className={`max-w-[88%] rounded-2xl px-3 py-2 text-[12.5px] leading-snug ${t.speaker === "agent" ? "rounded-tl-md bg-white/[0.07]" : "ml-auto rounded-tr-md bg-saffron-500/90 text-ink-950"}`}
                      >
                        <div className={`mb-0.5 text-[9.5px] font-semibold uppercase tracking-wider ${t.speaker === "agent" ? "text-sig-call" : "text-ink-950/60"}`}>
                          {t.speaker === "agent" ? "AI agent" : first}
                        </div>
                        {t.text}
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="absolute bottom-2 left-1/2 z-20 h-1 w-28 -translate-x-1/2 rounded-full bg-white/40" />
        </div>
      </div>
    </div>
  );
}

const fmtSecs = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
