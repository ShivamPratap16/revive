// Typed client for the Revive API. Same origin in production (FastAPI serves this app),
// proxied to uvicorn in development (see vite.config.ts).

export type Bucket = { key: string; count: number };
export type Summary = {
  period_days: number;
  since: string;
  sessions: {
    started: number;
    completed: number;
    completed_without_help: number;
    dropped_off: number;
    recovered: number;
    recovery_rate: number;
    median_minutes_to_recover: number | null;
  };
  funnel: { step: string; reached: number; pct_of_started: number }[];
  dropoffs_by_step: { step: string; dropped: number; recovered: number; recovery_rate: number }[];
  recovered_by_channel: Bucket[];
  by_language: { language: string; dropped: number; recovered: number; recovery_rate: number }[];
  calls: {
    placed: number;
    answered: number;
    answer_rate: number;
    avg_duration_seconds: number | null;
    by_status: Bucket[];
  };
  insights: {
    analysed: number;
    top_reasons: Bucket[];
    outcomes: Bucket[];
    do_not_call_requests: number;
    callbacks_requested: number;
    by_source: Bucket[];
    avg_llm_latency_ms: number | null;
  };
  dead_letter: number;
  daily: { date: string; started: number; dropped: number; recovered: number }[];
};

export type Turn = { speaker: "agent" | "user"; text: string; start_ms: number };
export type Insight = {
  outcome: string;
  dropoff_reason: string;
  sentiment: string;
  spoken_language: string;
  callback_requested_at: string | null;
  do_not_call: boolean;
  summary: string;
  key_quote: string;
  confidence: number;
};
export type Analysis = {
  source: "llm" | "rules";
  latency_ms: number | null;
  result: Insight;
  actions: string[] | null;
  notes: string[] | null;
  redactions: Record<string, number> | null;
  transcript?: Turn[];
  model?: string | null;
  prompt_version?: string | null;
};
export type TimelineItem = { at: string; kind: string; title: string; detail: string | null };
export type SessionView = {
  session: {
    name: string;
    phone: string;
    language: string;
    step: string;
    status: string;
    started_at: string;
  };
  timeline: TimelineItem[];
  analysis: Analysis | null;
  call: { status: string; transcript: Turn[]; duration_seconds: number | null } | null;
  finished: boolean;
};
export type TryRun = { session_id: string; name: string; language: string; step: string };
export type CallRow = {
  id: string;
  name: string;
  language: string;
  step: string | null;
  placed_at: string;
  duration_seconds: number | null;
  outcome: string | null;
  reason: string | null;
  do_not_call: boolean | null;
  callback_requested_at: string | null;
  summary: string | null;
  source: string;
  recovered: boolean;
};
export type CallDetail = {
  id: string;
  name: string;
  phone: string;
  step: string | null;
  status: string;
  placed_at: string;
  duration_seconds: number | null;
  transcript: Turn[];
  webhooks: { type: string; at: string; effect: string | null }[];
  analysis: Analysis | null;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { apiKey?: string }): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.apiKey) headers.set("X-API-Key", init.apiKey);
  const r = await fetch(path, { ...init, headers });
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, body?.error?.message ?? `Request failed (${r.status})`);
  return body as T;
}

export const api = {
  demoSummary: (days = 30) => request<Summary>(`/v1/demo/summary?days=${days}`),
  tenantSummary: (key: string, days = 30) =>
    request<Summary>(`/v1/analytics/summary?days=${days}`, { apiKey: key }),
  startTry: () => request<TryRun>("/v1/demo/try", { method: "POST" }),
  session: (id: string) => request<SessionView>(`/v1/demo/sessions/${id}`),
  calls: (limit = 25) => request<CallRow[]>(`/v1/demo/calls?limit=${limit}`),
  call: (id: string) => request<CallDetail>(`/v1/demo/calls/${id}`),
};

/** Live session updates over Server-Sent Events, falling back to polling if the stream
 *  can't be held open (some proxies and corporate networks cut long-lived responses). */
export function watchSession(id: string, onUpdate: (v: SessionView) => void): () => void {
  let stopped = false;
  let timer: number | undefined;
  let polling = false;

  const poll = async () => {
    if (stopped) return;
    try {
      const v = await api.session(id);
      onUpdate(v);
      if (v.finished) return;
    } catch {
      /* transient: try again */
    }
    timer = window.setTimeout(poll, 2500);
  };
  const startPolling = () => {
    if (!polling) {
      polling = true;
      poll();
    }
  };

  let es: EventSource | null = null;
  if ("EventSource" in window) {
    es = new EventSource(`/v1/demo/sessions/${id}/stream`);
    es.addEventListener("update", (e) => onUpdate(JSON.parse((e as MessageEvent).data)));
    es.addEventListener("done", () => es?.close());
    es.onerror = () => {
      es?.close();
      startPolling();
    };
  } else {
    startPolling();
  }
  return () => {
    stopped = true;
    es?.close();
    window.clearTimeout(timer);
  };
}
