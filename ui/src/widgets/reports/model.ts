// Reports as screens show them: which days can be picked, where downloads come from, how a
// report's state is said. Every figure comes from the server (docs/design/reports.md).

import type { DayReport, ReportDays } from "../../api/types";

export interface DayOption {
  day: string;
  label: string;
}

/** Today first (a live preview until it is kept at 16:00), then kept days, newest first. */
export function dayOptions(days: ReportDays | null): DayOption[] {
  if (!days) return [];
  const kept = [...days.kept].sort().reverse();
  const options: DayOption[] = [];
  if (!kept.includes(days.today)) options.push({ day: days.today, label: `${days.today} · today (preview)` });
  for (const d of kept) options.push({ day: d, label: d === days.today ? `${d} · today (kept)` : d });
  return options;
}

export function downloadUrl(accountId: string, day: string, format: "csv" | "pdf"): string {
  return `/api/accounts/${encodeURIComponent(accountId)}/reports/${day}?format=${format}`;
}

/** What kind of report this is, in words. */
export function reportState(r: DayReport): { text: string; tone: "good" | "dim" | "bad" } {
  if (r.final) return { text: "Kept after the close", tone: "good" };
  if (r.priced) return { text: "Preview: kept at 16:00 IST with closing prices", tone: "dim" };
  return { text: "Rebuilt without prices: see the notes", tone: "bad" };
}

/** The default history range: the last 30 days up to today. */
export function lastDays(today: string, n = 30): { start: string; end: string } {
  const end = new Date(`${today}T00:00:00Z`);
  const start = new Date(end.getTime() - n * 86_400_000);
  return { start: start.toISOString().slice(0, 10), end: today };
}
