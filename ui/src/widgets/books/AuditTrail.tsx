// Audit trail: everything that happened on the account, newest first, append-only (canvas
// boards 2 and 6, docs/design/ui.md §4). Click a row with an order = its whole timeline on the
// right. Live mode catches up from the event log after every order event.
//
//   GET /api/accounts/{a}/events               newest page (opening the screen)
//   GET …/events?before_seq=N                  "Older" (scrolling back)
//   GET …/events?after_seq=<newest seen>       live: after each order / kill event on the stream
//   GET …/orders/{id}/history                  the selected order's timeline
//
// The log is written by a background thread, so a catch-up right after an event can miss it:
// live mode asks twice (soon, and again a little later) and every few seconds while open.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AuditEvent } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { clock, price, qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { auditDetail, auditRow, CSV_HEADER, execType, matchesFilter, mergeEvents, toCsv, type AuditFilter } from "./model";

export interface AuditParams extends BaseParams {
  filter: AuditFilter;
  contract: string;
  live: boolean;
  orderId?: string; // the order whose timeline is open
}

const FILTERS: [AuditFilter, string][] = [
  ["all", "All"],
  ["fills", "Fills"],
  ["rejects", "Rejects"],
  ["cancels", "Cancels"],
  ["stops", "Stops"],
];
export const PAGE = 200;
export const CATCH_UP_MS = [300, 2000];
export const POLL_MS = 5000;

export function AuditTrail(props: WidgetProps<AuditParams>) {
  const accountId = useStore((s) => s.accountId);
  // made afresh for each account: its events, pages and newest seq never carry over
  return <AccountAudit key={accountId ?? ""} {...props} accountId={accountId} />;
}

function AccountAudit({ params, setParams, publish, accountId }: WidgetProps<AuditParams> & { accountId: string | null }) {
  const { rest, stream } = useApp();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [older, setOlder] = useState(true); // more pages behind the last one?
  const [error, setError] = useState("");
  const filter = params.filter ?? "all";
  const contract = params.contract ?? "";
  const live = params.live ?? true;
  const newest = useRef(0);
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}` : null;

  const take = useCallback((page: AuditEvent[]) => {
    if (page.length) newest.current = Math.max(newest.current, page[0]!.seq);
    setEvents((current) => mergeEvents(current, page));
  }, []);

  // the newest page (a new account is a new AccountAudit: it starts empty)
  useEffect(() => {
    if (!base) return;
    let alive = true;
    rest
      .get<AuditEvent[]>(`${base}/events?limit=${PAGE}`)
      .then((page) => {
        if (!alive) return;
        setError("");
        setOlder(page.length === PAGE);
        take(page);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [rest, base, take]);

  const catchUp = useCallback(() => {
    if (!base) return;
    rest
      .get<AuditEvent[]>(`${base}/events?after_seq=${newest.current}&limit=1000`)
      .then((page) => {
        setError("");
        take(page);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [rest, base, take]);

  // live: catch up after events on the stream, after reconnects, and every few seconds
  useEffect(() => {
    if (!live || !base) return;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const soon = () => {
      for (const ms of CATCH_UP_MS) {
        const t = setTimeout(() => {
          timers.delete(t);
          catchUp();
        }, ms);
        timers.add(t);
      }
    };
    const offs = [
      stream.on("order", (m) => {
        if (m.account_id === accountId) soon();
      }),
      stream.on("kill", soon),
      stream.onStatus((s) => {
        if (s === "open") catchUp();
      }),
    ];
    const poll = setInterval(catchUp, POLL_MS);
    return () => {
      for (const off of offs) off();
      for (const t of timers) clearTimeout(t);
      clearInterval(poll);
    };
  }, [live, base, stream, accountId, catchUp]);

  const loadOlder = () => {
    if (!base || !events.length) return;
    const last = events[events.length - 1]!.seq;
    rest
      .get<AuditEvent[]>(`${base}/events?before_seq=${last}&limit=${PAGE}`)
      .then((page) => {
        setOlder(page.length === PAGE);
        take(page);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const shown = useMemo(() => events.filter((e) => matchesFilter(e, filter, contract)), [events, filter, contract]);

  const exportCsv = () => {
    const csv = toCsv([CSV_HEADER, ...shown.map(auditRow)]);
    if (typeof URL.createObjectURL !== "function") return; // not in a browser (tests)
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `mocktrade-audit-${accountId}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="book audit">
      <div className="book-bar">
        {FILTERS.map(([key, label]) => (
          <button key={key} type="button" className={`book-tab${filter === key ? " on" : ""}`} aria-pressed={filter === key} onClick={() => setParams({ filter: key })}>
            {label}
          </button>
        ))}
        <input
          className="book-search"
          aria-label="Filter by contract"
          placeholder="Contract…"
          value={contract}
          onChange={(e) => setParams({ contract: e.target.value })}
        />
        <span className="book-spacer" />
        <button
          type="button"
          className={`btn small${live ? " on" : ""}`}
          aria-pressed={live}
          title={live ? "Updating as things happen: click to pause" : "Paused: click to follow new events"}
          onClick={() => setParams({ live: !live })}
        >
          {live ? "● Live" : "❚❚ Paused"}
        </button>
        <button type="button" className="btn small" disabled={!shown.length} onClick={exportCsv} title="Download the rows shown as CSV">
          CSV
        </button>
      </div>
      <div className="audit-main">
        <div className="audit-list">
          <div className="book-scroll">
            <div className="grid-head book-row audit-row" role="row">
              <span>Transact time</span>
              <span>Contract</span>
              <span>Order</span>
              <span>User</span>
              <span>Exec type</span>
              <span>Side</span>
              <span className="num">Qty</span>
              <span className="num">Filled</span>
              <span className="num">Price</span>
              <span>Detail</span>
            </div>
            <div className="grid-body" role="grid" aria-label="Audit trail">
              {error && <div className="empty-note bad">Couldn't load the audit trail: {error}</div>}
              {!error && !shown.length && <div className="empty-note">{events.length ? "Nothing matches the filter." : "No events yet."}</div>}
              {shown.map((e) => {
                const r = e.order?.request;
                const type = execType(e);
                const selected = !!e.client_order_id && e.client_order_id === params.orderId;
                return (
                  <div
                    key={e.seq}
                    role="row"
                    aria-selected={selected}
                    className={`row book-row audit-row${selected ? " selected" : ""}${r?.side === "SELL" ? " sell" : ""}`}
                    onClick={() => {
                      if (!e.client_order_id) return;
                      setParams({ orderId: e.client_order_id });
                      if (r) publish(r.instrument_id);
                    }}
                  >
                    <span className="dim">{clock(e.ts)}</span>
                    <span>{r ? shortName(r.instrument_id) : e.account_id === "firm" ? "(firm)" : ""}</span>
                    <span className="dim">{e.client_order_id}</span>
                    <span className="dim">{r?.user_id ?? String(e.extra.by ?? "")}</span>
                    <span className={`tone-${type.tone}`}>{type.text}</span>
                    <span className={r?.side === "BUY" ? "buy-text" : r?.side === "SELL" ? "sell-text" : ""}>{r ? (r.side === "BUY" ? "Buy" : "Sell") : ""}</span>
                    <span className="num">{r ? qty(r.qty) : ""}</span>
                    <span className="num">{e.order ? qty(e.order.filled_qty) : ""}</span>
                    <span className="num">{r ? (r.price_paise !== null ? price(r.price_paise) : r.order_type) : ""}</span>
                    <span className="dim" title={auditDetail(e)}>
                      {auditDetail(e)}
                    </span>
                  </div>
                );
              })}
              {older && events.length > 0 && (
                <button type="button" className="btn small audit-older" onClick={loadOlder}>
                  Older…
                </button>
              )}
            </div>
          </div>
        </div>
        {params.orderId && base && (
          <Timeline base={base} orderId={params.orderId} newest={events[0]?.seq ?? 0} onClose={() => setParams({ orderId: undefined })} />
        )}
      </div>
    </div>
  );
}

/** One order's whole life, oldest first: what was asked, every reply, every fill. */
function Timeline({ base, orderId, newest, onClose }: { base: string; orderId: string; newest: number; onClose: () => void }) {
  const { rest } = useApp();
  const [history, setHistory] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    rest
      .get<AuditEvent[]>(`${base}/orders/${encodeURIComponent(orderId)}/history`)
      .then((h) => {
        if (!alive) return;
        setError("");
        setHistory(h);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [rest, base, orderId, newest]); // `newest`: reload when the trail moves on
  const first = history?.[0]?.order?.request;
  return (
    <aside className="audit-timeline" aria-label={`Timeline of ${orderId}`}>
      <div className="book-bar">
        <b>{orderId}</b>
        <span className="dim">{first ? `${first.side} ${qty(first.qty)} ${shortName(first.instrument_id)}` : ""}</span>
        <span className="book-spacer" />
        <button type="button" className="row-action" aria-label="Close timeline" onClick={onClose}>
          ×
        </button>
      </div>
      {error && <div className="empty-note bad">{error}</div>}
      <ol className="timeline">
        {(history ?? []).map((e) => {
          const type = execType(e);
          return (
            <li key={e.seq}>
              <span className="dim">{clock(e.ts)}</span>
              <b className={`tone-${type.tone}`}>{type.text}</b>
              <span>{auditDetail(e)}</span>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

registerWidget<AuditParams>({
  kind: "audit",
  title: "Audit Trail",
  description: "Everything that happened on the account, with each order's timeline",
  component: AuditTrail,
  defaults: () => ({ link: "pink", filter: "all", contract: "", live: true }),
  link: "drives",
  size: { width: 760 },
});
