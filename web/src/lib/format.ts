export const pct = (x: number) => `${Math.round(x * 100)}%`;
export const label = (s: string | null | undefined) => (s ?? "").replaceAll("_", " ");
export const titleCase = (s: string | null | undefined) =>
  label(s).replace(/\b\w/g, (c) => c.toUpperCase());

const IST: Intl.DateTimeFormatOptions = {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
};
/** "2026-10-16T12:30:00+00:00" -> "16 Oct, 6:00 pm IST" */
export const istTime = (iso: string) => `${new Date(iso).toLocaleString("en-IN", IST)} IST`;
/** A wall-clock time already in IST, like "2026-10-16T18:00" -> "16 Oct, 6:00 pm IST" */
export const istLocal = (v: string | null) => (v ? istTime(`${v}+05:30`) : "—");
/** Replace ISO timestamps inside free text with readable IST times. */
export const humanTimes = (text: string) =>
  text.replace(/\d{4}-\d\d-\d\dT[\d:.]+(?:\+00:00|Z)/g, (iso) => istTime(iso));

export const sinceStart = (at: string, base: number) => {
  const s = Math.max(0, Math.round((new Date(at).getTime() - base) / 1000));
  return `+${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
export const minutesHuman = (m: number | null) =>
  m == null ? "—" : m >= 120 ? `${(m / 60).toFixed(1)} h` : `${Math.round(m)} min`;
export const LANG: Record<string, string> = { en: "English", hi: "Hindi", hinglish: "Hinglish" };
export const timeAgo = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
