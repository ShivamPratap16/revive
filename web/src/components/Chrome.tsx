import { BookOpen, Github, LayoutDashboard, Linkedin, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Button, LiveDot, Logo } from "./ui";

export const GITHUB = "https://github.com/ShivamPratap16/revive";
const LINKEDIN = "https://www.linkedin.com/in/shivam-pratap-raj-99280826b/";

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 12);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const link = ({ isActive }: { isActive: boolean }) =>
    `hidden items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors sm:inline-flex ${
      isActive ? "text-fg" : "text-fg-muted hover:text-fg"
    }`;
  return (
    <header
      className={`sticky top-0 z-50 transition-all duration-300 ${
        scrolled ? "border-b border-white/[0.06] bg-ink-950/75 backdrop-blur-xl" : "border-b border-transparent"
      }`}
    >
      <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link to="/" className="flex items-center gap-2.5" aria-label="Revive home">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight">Revive</span>
          <span className="ml-1 hidden items-center gap-1.5 rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-fg-muted md:inline-flex">
            <LiveDot /> live
          </span>
        </Link>
        <div className="flex items-center gap-1">
          <a href="/#live" className="hidden items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-fg-muted transition-colors hover:text-fg sm:inline-flex">
            <PlayCircle size={15} /> Live demo
          </a>
          <NavLink to="/dashboard" className={link}>
            <LayoutDashboard size={15} /> Dashboard
          </NavLink>
          <a href="/docs" className="hidden items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-fg-muted transition-colors hover:text-fg sm:inline-flex">
            <BookOpen size={15} /> API
          </a>
          <Button href={GITHUB} external variant="outline" className="ml-2 !px-3 !py-1.5">
            <Github size={15} /> <span className="hidden sm:inline">Source</span>
          </Button>
        </div>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-24 border-t border-white/[0.06]">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-3">
          <Logo className="h-8 w-8" />
          <div>
            <div className="text-sm font-medium">Built by Shivam Pratap Raj</div>
            <div className="text-xs text-fg-faint">Backend engineer · Python, FastAPI, PostgreSQL, Redis</div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-fg-muted">
          <a className="inline-flex items-center gap-1.5 hover:text-fg" href={GITHUB} target="_blank" rel="noreferrer"><Github size={15} /> GitHub</a>
          <a className="inline-flex items-center gap-1.5 hover:text-fg" href={LINKEDIN} target="_blank" rel="noreferrer"><Linkedin size={15} /> LinkedIn</a>
          <a className="hover:text-fg" href="/docs">API reference</a>
          <Link className="hover:text-fg" to="/dashboard">Dashboard</Link>
        </div>
      </div>
      <p className="mx-auto max-w-6xl px-4 pb-8 text-xs leading-relaxed text-fg-faint sm:px-6">
        Phone calls are made by a provider simulator; no real numbers are dialled. Call analysis uses Google Gemini. Demo users are synthetic.
      </p>
    </footer>
  );
}
