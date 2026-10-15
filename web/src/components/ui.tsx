import { motion, useInView, useMotionValue, useSpring } from "framer-motion";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

export function Logo({ className = "h-7 w-7" }: { className?: string }) {
  // An "R" whose leg turns back on itself: a user who left, coming back.
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" className="fill-ink-800" />
      <path
        d="M10 23V9h6.5a4.5 4.5 0 0 1 0 9H10m6 0 5 5"
        fill="none"
        stroke="#ffb224"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

type ButtonProps = {
  children: ReactNode;
  variant?: "primary" | "ghost" | "outline";
  href?: string;
  to?: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  external?: boolean;
};

export function Button({ children, variant = "primary", href, to, onClick, disabled, className = "", external }: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50";
  const styles = {
    primary:
      "bg-saffron-500 text-ink-950 hover:bg-saffron-400 shadow-[0_0_0_1px_rgba(255,178,36,.4),0_8px_24px_-8px_rgba(255,178,36,.6)]",
    outline: "border border-white/10 bg-white/[0.03] text-fg hover:border-white/20 hover:bg-white/[0.06]",
    ghost: "text-fg-muted hover:text-fg",
  }[variant];
  const cls = `${base} ${styles} ${className}`;
  if (to) return <Link to={to} className={cls}>{children}</Link>;
  if (href)
    return (
      <a href={href} className={cls} {...(external ? { target: "_blank", rel: "noreferrer" } : {})}>
        {children}
      </a>
    );
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={cls}>
      {children}
    </button>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "ai" | "accent" }) {
  const tones = {
    neutral: "bg-white/[0.06] text-fg-muted ring-white/10",
    good: "bg-sig-call/10 text-sig-call ring-sig-call/25",
    warn: "bg-saffron-500/10 text-saffron-400 ring-saffron-500/25",
    bad: "bg-sig-bad/10 text-sig-bad ring-sig-bad/25",
    ai: "bg-sig-ai/10 text-sig-ai ring-sig-ai/25",
    accent: "bg-saffron-500/10 text-saffron-400 ring-saffron-500/30",
  }[tone];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tones}`}>
      {children}
    </span>
  );
}

export function LiveDot({ className = "" }: { className?: string }) {
  return (
    <span className={`relative inline-flex h-2 w-2 ${className}`}>
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sig-call opacity-60" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-sig-call" />
    </span>
  );
}

/** Fades and lifts children into place the first time they scroll into view. */
export function Reveal({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.6, delay, ease: [0.21, 0.6, 0.35, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** A number that counts up when it becomes visible. */
export function CountUp({ value, format = (n) => Math.round(n).toLocaleString("en-IN") }: { value: number; format?: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const spring = useSpring(mv, { duration: 1400, bounce: 0 });
  const [text, setText] = useState(format(0));
  useEffect(() => {
    if (inView) mv.set(value);
  }, [inView, value, mv]);
  useEffect(() => spring.on("change", (v) => setText(format(v))), [spring, format]);
  return <span ref={ref} className="num">{text}</span>;
}

export function SectionHeading({ eyebrow, title, children, center }: { eyebrow: string; title: ReactNode; children?: ReactNode; center?: boolean }) {
  return (
    <Reveal className={`mb-10 max-w-2xl ${center ? "mx-auto text-center" : ""}`}>
      <div className="eyebrow mb-3">{eyebrow}</div>
      <h2 className="h-section">{title}</h2>
      {children && <p className="mt-4 text-lg leading-relaxed text-fg-muted">{children}</p>}
    </Reveal>
  );
}
