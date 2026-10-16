// The square-off assistant in the Positions bar: open intraday (MIS) positions before the
// broker's own square-off. The cut-off times and the window come from the server (risk/
// squareoff.py); this only asks, shows and confirms.
//
//   [Square off MIS (2) · 15:25]  ─click─►  [Confirm: close 2 MIS]  ─click─►  closing orders

import { useEffect, useMemo, useState } from "react";
import type { SquareOffStatus } from "../../api/types";
import { useApp, useNow, useStore } from "../../app/context";

export function SquareOffButton() {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const positions = useStore((s) => (s.accountId ? s.books[s.accountId]?.positions : undefined));
  const openMis = useMemo(() => Object.values(positions ?? {}).filter((p) => p.product === "MIS" && p.qty !== 0).length, [positions]);
  const tick = useNow(30_000); // the minutes left move on their own
  const [status, setStatus] = useState<SquareOffStatus | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}/square-off` : null;

  useEffect(() => {
    if (!base) return;
    let live = true;
    rest
      .get<SquareOffStatus>(base)
      .then((s) => live && setStatus(s))
      .catch(() => live && setStatus(null)); // a nicety: positions still close one by one
    return () => {
      live = false;
    };
  }, [rest, base, openMis, tick]);

  useEffect(() => setArmed(false), [openMis, accountId]); // a confirmation is for what was shown

  const rows = status?.positions ?? [];
  if (!base || !rows.length) return null;
  const cutoff = rows.map((r) => r.cutoff).filter(Boolean).sort()[0] ?? "";
  const soonest = Math.min(...rows.map((r) => r.minutes_left ?? Infinity));
  const urgent = status!.window_open;
  const title = `${rows.map((r) => `${r.instrument_id} ${r.qty > 0 ? "+" : ""}${r.qty}`).join(", ")}: the broker squares off intraday positions at ${cutoff} IST${
    Number.isFinite(soonest) && urgent ? ` (in ${soonest} min)` : ""
  }`;

  const send = () => {
    setBusy(true);
    rest
      .post<{ closed: { instrument_id: string; orders: string[]; refused: string[] }[] }>(base, { confirm: true })
      .then((r) => {
        for (const c of r.closed) {
          const text = c.refused.length ? `Square-off ${c.instrument_id}: refused: ${c.refused.join("; ")}` : `Square-off ${c.instrument_id}: sent ${c.orders.join(", ")}`;
          store.addMessage(c.refused.length ? "ERROR" : "INFO", c.instrument_id, text);
        }
      })
      .catch((e: unknown) => store.addMessage("ERROR", "", `Square-off failed: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => {
        setBusy(false);
        setArmed(false);
      });
  };

  return armed ? (
    <button type="button" className="btn small danger" disabled={busy} onClick={send} title={title}>
      Confirm: close {rows.length} MIS
    </button>
  ) : (
    <button type="button" className={`btn small${urgent ? " danger" : ""}`} onClick={() => setArmed(true)} title={title}>
      Square off MIS ({rows.length}){cutoff ? ` · ${cutoff}` : ""}
    </button>
  );
}
