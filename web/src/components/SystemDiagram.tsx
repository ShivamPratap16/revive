import { useEffect, useState } from "react";

/** The pipeline stages the diagram can show. Each lights up the parts of the system it uses. */
export type Stage = "ingest" | "detect" | "nudge" | "call" | "webhook" | "ai" | "action" | "idle";

type NodeDef = { id: string; x: number; y: number; title: string; sub: string };
const W = 132;
const H = 56;
const NODES: NodeDef[] = [
  { id: "phone", x: 18, y: 36, title: "User's phone", sub: "Ravi" },
  { id: "app", x: 18, y: 182, title: "Fintech app", sub: "KreditBee" },
  { id: "api", x: 226, y: 182, title: "API", sub: "FastAPI" },
  { id: "pg", x: 430, y: 182, title: "PostgreSQL", sub: "source of truth" },
  { id: "worker", x: 226, y: 322, title: "Worker", sub: "arq · async jobs" },
  { id: "redis", x: 430, y: 322, title: "Redis", sub: "queue · rate limit" },
  { id: "voice", x: 610, y: 36, title: "Voice provider", sub: "places calls" },
  { id: "gemini", x: 610, y: 182, title: "Gemini", sub: "reads the call" },
  { id: "push", x: 610, y: 322, title: "Push", sub: "in-app nudge" },
];

const EDGES: Record<string, string> = {
  "phone-app": "M84 92 V182",
  "app-api": "M150 210 H226",
  "api-pg": "M358 210 H430",
  "worker-pg": "M326 322 V286 H496 V238",
  "worker-redis": "M358 350 H430",
  "worker-push": "M292 378 V402 H676 V378",
  "push-phone": "M742 350 H756 V14 H84 V36",
  "worker-voice": "M358 334 H392 V140 H600 V64 H610",
  "voice-phone": "M610 56 H150",
  "voice-api": "M640 92 V160 H330 V182",
  "worker-gemini": "M342 322 V302 H584 V210 H610",
};

const STAGES: Record<Stage, { nodes: string[]; edges: string[]; color: string; caption: string }> = {
  ingest: { nodes: ["phone", "app", "api", "pg"], edges: ["phone-app", "app-api", "api-pg"], color: "#58a6ff", caption: "App reports a KYC event" },
  detect: { nodes: ["worker", "pg"], edges: ["worker-pg"], color: "#ffb224", caption: "Worker spots a stuck user" },
  nudge: { nodes: ["worker", "redis", "push", "phone"], edges: ["worker-redis", "worker-push", "push-phone"], color: "#2dd4bf", caption: "In-app nudge goes out" },
  call: { nodes: ["worker", "voice", "phone"], edges: ["worker-voice", "voice-phone"], color: "#3fb950", caption: "AI voice call is placed" },
  webhook: { nodes: ["voice", "api", "pg"], edges: ["voice-api", "api-pg"], color: "#8b9bb4", caption: "Signed webhooks report back" },
  ai: { nodes: ["worker", "gemini", "pg"], edges: ["worker-gemini", "worker-pg"], color: "#a78bfa", caption: "Gemini reads the conversation" },
  action: { nodes: ["worker", "pg", "redis"], edges: ["worker-pg", "worker-redis"], color: "#ffb224", caption: "Revive acts on what it heard" },
  idle: { nodes: [], edges: [], color: "#ffb224", caption: "" },
};

const CYCLE: Stage[] = ["ingest", "detect", "nudge", "call", "webhook", "ai", "action"];

export function useCycle(enabled: boolean, ms = 1900): Stage {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const t = window.setInterval(() => setI((x) => (x + 1) % CYCLE.length), ms);
    return () => window.clearInterval(t);
  }, [enabled, ms]);
  return CYCLE[i];
}

export function SystemDiagram({ stage, showCaption = true, className = "", user = "Ravi" }: { stage: Stage; showCaption?: boolean; className?: string; user?: string }) {
  const s = STAGES[stage];
  return (
    <div className={className}>
      <svg viewBox="0 0 760 420" className="h-auto w-full" role="img" aria-label={`System diagram: ${s.caption || "Revive architecture"}`}>
        <defs>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Revive's own boundary */}
        <rect x="206" y="160" width="378" height="248" rx="16" fill="rgba(255,178,36,0.025)" stroke="rgba(255,178,36,0.22)" strokeDasharray="5 6" />
        <text x="222" y="398" className="fill-saffron-500/70 font-mono text-[10px] tracking-[0.16em]">REVIVE</text>

        {Object.entries(EDGES).map(([id, d]) => {
          const on = s.edges.includes(id);
          return (
            <g key={id}>
              <path id={`edge-${id}`} d={d} fill="none" stroke={on ? s.color : "rgba(255,255,255,0.09)"} strokeWidth={on ? 2 : 1.25} strokeOpacity={on ? 0.75 : 1} style={{ transition: "stroke .4s, stroke-width .4s" }} />
              {on &&
                [0, 0.45].map((delay) => (
                  <circle key={delay} r="4" fill={s.color} filter="url(#glow)">
                    <animateMotion dur="1.4s" begin={`${delay}s`} repeatCount="indefinite" keyPoints="0;1" keyTimes="0;1" calcMode="linear">
                      <mpath href={`#edge-${id}`} />
                    </animateMotion>
                  </circle>
                ))}
            </g>
          );
        })}

        {NODES.map((n) => {
          const on = s.nodes.includes(n.id);
          return (
            <g key={n.id} style={{ transition: "opacity .4s" }} opacity={stage === "idle" || on ? 1 : 0.55}>
              <rect x={n.x} y={n.y} width={W} height={H} rx="12" fill={on ? "#161c25" : "#0d1117"} stroke={on ? s.color : "rgba(255,255,255,0.09)"} strokeWidth={on ? 1.6 : 1} style={{ transition: "all .4s" }} />
              <circle cx={n.x + 16} cy={n.y + 21} r="3.5" fill={on ? s.color : "#2b3442"} style={{ transition: "fill .4s" }} />
              <text x={n.x + 27} y={n.y + 25} className="fill-fg text-[13px] font-medium">{n.title}</text>
              <text x={n.x + 16} y={n.y + 43} className="fill-fg-faint text-[11px]">{n.id === "phone" ? user : n.sub}</text>
            </g>
          );
        })}
      </svg>
      {showCaption && (
        <div className="mt-2 flex h-6 items-center justify-center gap-2 text-sm text-fg-muted">
          {s.caption && (
            <>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.color }} />
              <span key={stage} className="animate-[fadeIn_.4s_ease]">{s.caption}</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
