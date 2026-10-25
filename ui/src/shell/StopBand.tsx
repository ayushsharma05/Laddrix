// The STOP band (#262): what the STOP did, in the server's own words, and Flatten.
//
//   trading stopped (account or firm) ─► GET /api/accounts/{a}/flatten
//     { stop_report.lines, positions [{qty, unknown}], can_flatten, explain }
//   [Flatten (N)] ─► [Confirm: close N positions] ─► POST …/flatten {confirm: true}
//     ─► one message per position: what was sent, refused, left open or not closed
//
// STOP never closes positions; Flatten does, while trading stays stopped, and only with orders
// that can't reverse anything. A position whose size isn't known is never flattened: the
// server says so and the band shows it.

import { useCallback, useEffect, useState } from "react";
import { useApp, useStore } from "../app/context";

export interface FlattenRow {
  instrument_id: string;
  product: string;
  qty: number;
  closing_qty: number;
  unknown: string | null;
}

export interface FlattenStatus {
  stopped: boolean;
  stop: { reason: string; by: string; at: string | null } | null;
  stop_report: { lines: string[] } | null;
  positions: FlattenRow[];
  can_flatten: boolean;
  running: boolean;
  explain: string[];
}

interface FlattenResult {
  positions: { instrument_id: string; orders: string[]; refused: string[]; left_open: number; closed: boolean; summary: string }[];
  summary: string;
}

export function StopBand({ firm }: { firm: boolean }) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const kill = useStore((s) => (s.accountId ? s.books[s.accountId]?.kill : undefined));
  const positions = useStore((s) => (s.accountId ? s.books[s.accountId]?.positions : undefined));
  const [status, setStatus] = useState<FlattenStatus | null>(null);
  // a confirmation is only for the positions it was shown with (derived: a change disarms it)
  const [armedFor, setArmedFor] = useState<{ positions: typeof positions } | null>(null);
  const armed = armedFor !== null && armedFor.positions === positions;
  const setArmed = (on: boolean) => setArmedFor(on ? { positions } : null);
  const [busy, setBusy] = useState(false);
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}/flatten` : "";

  const load = useCallback(() => {
    if (!base) return;
    rest
      .get<FlattenStatus>(base)
      .then(setStatus)
      .catch(() => setStatus(null)); // the band still says trading is stopped
  }, [rest, base]);

  useEffect(load, [load, kill?.engaged, positions]);

  if (!accountId) return null;
  const open = status?.positions ?? [];
  const closable = open.filter((p) => p.unknown === null);
  const lines = status?.stop_report?.lines ?? [];

  const flatten = () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    rest
      .post<FlattenResult>(base, { confirm: true })
      .then((r) => {
        if (!r.positions.length) store.addMessage("INFO", "", `Flatten: ${r.summary}`);
        for (const p of r.positions) {
          const bad = !p.closed || p.refused.length > 0 || p.left_open > 0;
          store.addMessage(bad ? "ERROR" : "INFO", p.instrument_id, `Flatten: ${p.summary}`);
        }
      })
      .catch((e: unknown) => store.addMessage("ERROR", "", `Flatten failed: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => {
        setBusy(false);
        setArmed(false);
        load();
      });
  };

  return (
    <div role="alert" className="band stopped stop-band">
      <b>{firm ? "ALL TRADING STOPPED (firm)" : `TRADING STOPPED · ${accountId}`}</b>
      <span className="grow" title={[...lines, ...(status?.explain ?? [])].join("\n")}>
        {lines.length ? lines.slice(1).join(" · ") : `${kill?.reason ?? ""}${kill?.by ? ` · by ${kill.by}` : ""} · new orders blocked · positions kept`}
      </span>
      {open
        .filter((p) => p.unknown !== null)
        .map((p) => (
          <span key={`${p.instrument_id}|${p.product}`} className="strong" title={p.unknown ?? ""}>
            {p.instrument_id.split(":").pop()} {p.product}: size not known, close it at the broker
          </span>
        ))}
      {closable.length > 0 && (
        <button
          type="button"
          className="btn small danger"
          disabled={busy || status?.running || !status?.can_flatten}
          title={status?.explain[1] ?? ""}
          onClick={flatten}
        >
          {armed ? `Confirm: close ${closable.length} position${closable.length === 1 ? "" : "s"}` : `Flatten (${closable.length})`}
        </button>
      )}
    </div>
  );
}
