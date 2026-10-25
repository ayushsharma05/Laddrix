// Reports: the end-of-day report of a day (kept after the close, or today's preview) and the
// P&L history by day, instrument or strategy, with CSV and PDF downloads.
//
//   [Day] History   2026-10-13 ▾   Kept after the close            CSV  PDF  Refresh
//   Gross +₹7,500.00   Charges ₹1,111.52   Net +₹6,388.48   Turnover ₹37,65,000.00   Trades 2
//   Position  Prod  In  Bought  Sold  Out  Last  Gross  Charges  Net
//   Strategy  Trades  Gross  Charges  Net
//   Trades … · Rejected … · Alerts …

import { useCallback, useEffect, useMemo, useState } from "react";
import type { DayReport, PnlHistory, ReportDays } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { clock, price, qty, rupees, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { signedRupees } from "../books/model";
import { dayOptions, downloadUrl, lastDays, reportState } from "./model";

export interface ReportsParams extends BaseParams {
  mode?: "day" | "history";
  by?: PnlHistory["by"];
}

const tone = (n: number) => (n > 0 ? "good" : n < 0 ? "bad" : "");

export function Reports({ params, setParams, publish }: WidgetProps<ReportsParams>) {
  const { rest } = useApp();
  const accountId = useStore((s) => s.accountId);
  const mode = params.mode ?? "day";
  const [days, setDays] = useState<ReportDays | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [report, setReport] = useState<DayReport | null>(null);
  const [history, setHistory] = useState<PnlHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loads, setLoads] = useState(0); // Refresh asks again
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}` : null;
  // today's preview follows the day: every fill changes the day's charges, so read it again
  const turnover = useStore((s) => (s.accountId ? (s.books[s.accountId]?.charges?.turnover_paise ?? 0) : 0));
  const follow = days !== null && day === days.today ? turnover : 0;

  useEffect(() => {
    if (!base) return;
    let live = true;
    rest
      .get<ReportDays>(`${base}/reports`)
      .then((d) => {
        if (!live) return;
        setDays(d);
        setDay((old) => old ?? d.today);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [rest, base, loads]);

  useEffect(() => {
    if (!base || !day || mode !== "day") return;
    let live = true;
    rest
      .get<DayReport>(`${base}/reports/${day}`)
      .then((r) => {
        if (!live) return;
        setReport(r);
        setError(null); // an earlier failure is over once an answer comes
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [rest, base, day, mode, loads, follow]);

  const by = params.by ?? "day";
  useEffect(() => {
    if (!base || !days || mode !== "history") return;
    let live = true;
    const { start, end } = lastDays(days.today);
    rest
      .get<PnlHistory>(`${base}/pnl?by=${by}&start=${start}&end=${end}`)
      .then((h) => {
        if (!live) return;
        setHistory(h);
        setError(null);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [rest, base, days, mode, by, loads]);

  const options = useMemo(() => dayOptions(days), [days]);
  const refresh = useCallback(() => setLoads((n) => n + 1), []);

  if (!accountId) return <div className="empty-note">No account selected.</div>;
  return (
    <div className="book reports">
      <div className="book-bar">
        {(["day", "history"] as const).map((m) => (
          <button key={m} type="button" className={`book-tab${mode === m ? " on" : ""}`} aria-pressed={mode === m} onClick={() => setParams({ mode: m })}>
            {m === "day" ? "Day" : "History"}
          </button>
        ))}
        {mode === "day" ? (
          <select aria-label="Report day" value={day ?? ""} onChange={(e) => setDay(e.target.value)}>
            {options.map((o) => (
              <option key={o.day} value={o.day}>
                {o.label}
              </option>
            ))}
          </select>
        ) : (
          <select aria-label="P&L by" value={by} onChange={(e) => setParams({ by: e.target.value as PnlHistory["by"] })}>
            <option value="day">by day</option>
            <option value="instrument">by instrument</option>
            <option value="strategy">by strategy</option>
          </select>
        )}
        {mode === "day" && report && <span className={`tone-${reportState(report).tone}`}>{reportState(report).text}</span>}
        <span className="book-spacer" />
        {mode === "day" && day && (
          <>
            <a className="btn small" href={downloadUrl(accountId, day, "csv")} download>
              CSV
            </a>
            <a className="btn small" href={downloadUrl(accountId, day, "pdf")} download>
              PDF
            </a>
          </>
        )}
        <button type="button" className="btn small" onClick={refresh}>
          Refresh
        </button>
      </div>
      <div className="book-scroll reports-body">
        {error && (
          <div className="empty-note tone-bad" role="alert">
            {error}
          </div>
        )}
        {mode === "day" && report && <DayView report={report} publish={publish} />}
        {mode === "history" && history && <HistoryView history={history} />}
      </div>
    </div>
  );
}

function DayView({ report: r, publish }: { report: DayReport; publish: (id: string) => void }) {
  const t = r.totals;
  return (
    <>
      <div className="report-totals" aria-label="Day totals">
        <span>
          Gross <b className={tone(t.gross_paise)}>{signedRupees(t.gross_paise)}</b>
        </span>
        <span title={`${r.charge_lines.filter((l) => l.paise).map((l) => `${l.label} ${price(l.paise)}`).join(" · ")} (${r.schedule})`}>
          Charges <b>{rupees(t.charges_paise)}</b>
        </span>
        <span>
          Net <b className={tone(t.net_paise)}>{signedRupees(t.net_paise)}</b>
        </span>
        <span>
          Turnover <b>{rupees(t.turnover_paise)}</b>
        </span>
        <span>
          Trades <b>{t.trades}</b>
        </span>
      </div>
      {r.prices_as_of && (
        <div className="empty-note dim" title="Open positions are valued at the last price received for each; this is the oldest of them">
          Open positions valued at prices as of {clock(r.prices_as_of, false)}
        </div>
      )}
      {r.notes.map((n) => (
        <div key={n} className="empty-note tone-bad">
          {n}
        </div>
      ))}
      <h4>Positions</h4>
      <div className="grid-head book-row report-pos-row" role="row">
        <span>Position</span>
        <span>Prod</span>
        <span className="num">In</span>
        <span className="num">Bought</span>
        <span className="num">Sold</span>
        <span className="num">Out</span>
        <span className="num">Last</span>
        <span className="num">Gross</span>
        <span className="num">Charges</span>
        <span className="num">Net</span>
      </div>
      <div className="grid-body" role="grid" aria-label="Report positions">
        {!r.positions.length && <div className="empty-note">No positions on this day.</div>}
        {r.positions.map((p) => (
          <div key={`${p.instrument_id}|${p.product}`} role="row" className="row book-row report-pos-row" onClick={() => publish(p.instrument_id)}>
            <span>{shortName(p.instrument_id)}</span>
            <span className="dim">{p.product}</span>
            <span className="num">{qty(p.carried_qty)}</span>
            <span className="num">{qty(p.buy_qty)}</span>
            <span className="num">{qty(p.sell_qty)}</span>
            <span className="num">{qty(p.closing_qty)}</span>
            <span className="num">{p.mark_paise !== null ? price(p.mark_paise) : "–"}</span>
            <span className={`num ${tone(p.gross_paise)}`}>{signedRupees(p.gross_paise)}</span>
            <span className="num dim">{price(p.charges_paise)}</span>
            <span className={`num ${tone(p.net_paise)}`}>{signedRupees(p.net_paise)}</span>
          </div>
        ))}
      </div>
      <h4>By strategy</h4>
      <div className="grid-body" role="grid" aria-label="Report strategies">
        {r.strategies.map((s) => (
          <div key={s.strategy} role="row" className="row book-row report-strategy-row">
            <span>{s.strategy}</span>
            <span className="num">{s.trades} trades</span>
            <span className={`num ${tone(s.gross_paise)}`}>{signedRupees(s.gross_paise)}</span>
            <span className="num dim">{price(s.charges_paise)}</span>
            <span className={`num ${tone(s.net_paise)}`}>{signedRupees(s.net_paise)}</span>
          </div>
        ))}
      </div>
      <h4>Trades ({r.trades.length})</h4>
      <div className="grid-body" role="grid" aria-label="Report trades">
        {r.trades.map((x, n) => (
          <div key={n} role="row" className="row book-row report-trade-row">
            <span className="dim">{clock(x.ts, false)}</span>
            <span>{shortName(x.instrument_id)}</span>
            <span className={x.side === "BUY" ? "buy-text" : "sell-text"}>{x.side}</span>
            <span className="num">{qty(x.qty)}</span>
            <span className="num">{price(x.price_paise)}</span>
            <span className="dim">{x.strategy}</span>
          </div>
        ))}
      </div>
      <h4>Rejected ({r.rejects.length}) · Alerts ({r.alerts.length})</h4>
      <ul className="report-notes" aria-label="Rejects and alerts">
        {r.rejects.map((n, i) => (
          <li key={`r${i}`} className="tone-bad">
            {clock(n.ts, false)} rejected: {n.detail}
          </li>
        ))}
        {r.alerts.map((n, i) => (
          <li key={`a${i}`}>
            {clock(n.ts, false)} {n.kind}: {n.detail}
          </li>
        ))}
      </ul>
    </>
  );
}

function HistoryView({ history: h }: { history: PnlHistory }) {
  return (
    <>
      <div className="report-totals" aria-label="Period totals">
        <span>
          {h.from} → {h.to}
        </span>
        <span>
          Gross <b className={tone(h.total.gross_paise)}>{signedRupees(h.total.gross_paise)}</b>
        </span>
        <span>
          Charges <b>{rupees(h.total.charges_paise)}</b>
        </span>
        <span>
          Net <b className={tone(h.total.net_paise)}>{signedRupees(h.total.net_paise)}</b>
        </span>
      </div>
      {h.missing_days.length > 0 && (
        <div className="empty-note tone-bad" title={h.missing_days.join(", ")}>
          {h.missing_days.length} trading day{h.missing_days.length > 1 ? "s" : ""} without a kept report (not counted)
        </div>
      )}
      <div className="grid-body" role="grid" aria-label="P&L history">
        {!h.rows.length && <div className="empty-note">No kept reports in this period.</div>}
        {h.rows.map((r) => (
          <div key={r.key} role="row" className="row book-row report-strategy-row">
            <span>{h.by === "instrument" ? shortName(r.key) : r.key}</span>
            <span className="num">{r.trades} trades</span>
            <span className={`num ${tone(r.gross_paise)}`}>{signedRupees(r.gross_paise)}</span>
            <span className="num dim">{price(r.charges_paise)}</span>
            <span className={`num ${tone(r.net_paise)}`}>{signedRupees(r.net_paise)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

registerWidget<ReportsParams>({
  kind: "reports",
  title: "Reports",
  description: "End-of-day report (CSV/PDF) and P&L history by day, instrument or strategy",
  component: Reports,
  defaults: () => ({ link: "pink", mode: "day" }),
  link: "drives",
});
