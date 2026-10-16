// "Restore stop" in an UNPROTECTED band (#264): starts the stop the position had again, sized
// to what is open now, after two clicks. The server says what it would start, or why it can't
// now (an old order still working or unanswered, another stop there, the size in doubt…).
//
//   band "unprotected:<account>:<instrument>" ─► GET /api/accounts/{a}/protection
//     [Restore stop] ─► [Confirm: TRAILING_STOP SELL 75 …] ─► POST …/protection/restore
//     the band clears by itself once the stop is working

import { useEffect, useState } from "react";
import { useApp } from "../app/context";

interface Row {
  instrument_id: string;
  restore_as: string | null;
  blocked_by: string | null;
}

export function RestoreStop({ alertKey }: { alertKey: string }) {
  const { rest, store } = useApp();
  const [, accountId = "", ...rest_] = alertKey.split(":");
  const instrumentId = rest_.join(":");
  const base = `/api/accounts/${encodeURIComponent(accountId)}/protection`;
  const [row, setRow] = useState<Row | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    rest
      .get<{ unprotected: Row[] }>(base)
      .then((r) => live && setRow(r.unprotected.find((x) => x.instrument_id === instrumentId) ?? null))
      .catch(() => live && setRow(null)); // can't see it (another account): no button
    return () => {
      live = false;
    };
  }, [rest, base, instrumentId]);

  if (!row) return null;
  if (row.blocked_by) return <span className="dim">Restore stop: not now ({row.blocked_by})</span>;
  const restore = () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    rest
      .post(`${base}/restore`, { instrument_id: instrumentId, confirm: true })
      .then(() => store.addMessage("INFO", instrumentId, `Stop restored: ${row.restore_as}`))
      .catch((e: unknown) => store.addMessage("ERROR", instrumentId, `Restore stop refused: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => {
        setBusy(false);
        setArmed(false);
      });
  };
  return (
    <button type="button" className="btn small danger" disabled={busy} title={row.restore_as ?? ""} onClick={restore}>
      {armed ? `Confirm: ${row.restore_as}` : "Restore stop"}
    </button>
  );
}
