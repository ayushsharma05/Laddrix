// STOP and resume: the only dialogs in the terminal (docs/design/ui.md §5).
//
//   STOP NOW ─► POST /api/accounts/{a}/stop {reason} ─► the switch engages first (no new
//   orders), then working orders are cancelled. Positions are kept. The stream's "kill"
//   message turns the band red on every screen; the band shows the server's report of what
//   the STOP did, and Flatten (shell/StopBand.tsx, #262).

import { useMemo, useState } from "react";
import { ApiError } from "../api/rest";
import { useApp, useStore } from "../app/context";

interface Props {
  mode: "stop" | "resume";
  onClose: () => void;
}

export function StopDialog({ mode, onClose }: Props) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const me = useStore((s) => s.me);
  const orders = useStore((s) => (s.accountId ? s.books[s.accountId]?.orders : undefined));
  const positions = useStore((s) => (s.accountId ? s.books[s.accountId]?.positions : undefined));
  const [reason, setReason] = useState("manual stop");
  const [firm, setFirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const roles = me?.user.roles ?? [];
  const canFirm = roles.includes("risk") || roles.includes("admin");
  const working = useMemo(
    () => Object.values(orders ?? {}).filter((o) => ["PENDING", "OPEN", "PARTIAL"].includes(o.status)).length,
    [orders],
  );
  const open = useMemo(() => Object.values(positions ?? {}).filter((p) => p.qty !== 0).length, [positions]);

  if (!accountId) return null;
  const base = `/api/accounts/${encodeURIComponent(accountId)}`;

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (mode === "stop") {
        const result = await rest.post<{ failed_cancels: { client_order_id: string; reason: string }[]; reports?: { account_id: string; lines: string[] }[] }>(
          firm ? "/api/firm/stop" : `${base}/stop`,
          { reason: reason.trim() || "manual stop" },
        );
        for (const f of result.failed_cancels) {
          store.addMessage("ERROR", "", `Couldn't cancel ${f.client_order_id}: ${f.reason}. Check it at the broker.`);
        }
        // exactly what the STOP did, in the server's words (#262)
        for (const r of result.reports ?? []) store.addMessage("INFO", "", `${r.account_id}: ${r.lines.join("; ")}`);
      } else {
        await rest.post(`${base}/resume`);
      }
      onClose();
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const stop = mode === "stop";
  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <section
        className={`window dialog ${stop ? "danger" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="stop-title"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "BUTTON") {
            e.preventDefault();
            void run();
          }
        }}
      >
        <div className="window-bar">
          <span id="stop-title" className="strong grow">
            {stop ? `STOP trading on ${firm ? "the whole firm" : accountId}?` : `Resume trading on ${accountId}?`}
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="dialog-body">
          {stop ? (
            <ul className="facts">
              <li>
                <b>Cancelled now:</b> {firm ? "every working order on every account" : `${working} working order${working === 1 ? "" : "s"}`}
              </li>
              <li>
                <b>Blocked:</b> every new order and change until someone resumes
              </li>
              <li>
                <b>Kept:</b> {firm ? "all positions" : `${open} open position${open === 1 ? "" : "s"}`} (STOP doesn't close them; Flatten in the red band does, while trading stays stopped)
              </li>
            </ul>
          ) : (
            <p className="dim">New orders will be accepted again on {accountId}.</p>
          )}
          {stop && (
            <label className="field">
              Reason (kept in the audit trail)
              <input
                className="input"
                value={reason}
                maxLength={200}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
          )}
          {stop && canFirm && (
            <label className="check">
              <input type="checkbox" checked={firm} onChange={(e) => setFirm(e.target.checked)} /> Stop the whole firm
            </label>
          )}
          {error && (
            <div role="alert" className="alert bad">
              {error}
            </div>
          )}
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              Back
            </button>
            <button
              type="button"
              className={stop ? "btn stop-now" : "btn primary"}
              autoFocus
              disabled={busy}
              onClick={() => void run()}
            >
              {busy ? "…" : stop ? "STOP NOW" : "RESUME"}
            </button>
          </div>
          <p className="hint">{stop ? "Enter = Stop now · Esc = back" : "Enter = Resume · Esc = back"}</p>
        </div>
      </section>
    </div>
  );
}
