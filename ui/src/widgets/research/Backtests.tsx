// Backtests: start an algo over recorded days, follow the run, read its results, compare runs
// (K11.4; the runner is K11.3, docs/design/research.md).
//
//   [Runs] New  Compare                                                     Refresh
//   ☐ Run                          State                 Net         Days
//   ☐ SLICER BUY 150 NIFTY26OCTFUT Running: 2 of 5 days               5
//   ── the picked run ──────────────────────────────────────────────────────────────
//   Net +₹1,200.00  Gross …  Charges …  Max drawdown …  Winning days 3 of 5 (60.0%)  Trades 10
//   [cumulative net, one point per day]
//   Day         Trades  Gross   Charges  Net     CSV PDF       (a day: its P&L curve, trades)
//
// The New form is drawn from the server (GET /api/research/backtests/form): the algos and
// their settings, the recorded days, the products and the defaults. Nothing here knows an algo.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/rest";
import type { BacktestForm, BacktestRun, Side } from "../../api/types";
import { useApp, useInstrument } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, price, qty, rupees, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { ParamInput } from "../algos/AlgoManager";
import { byFamily, familyLabel, initialValues, rupeeText, type FormValues } from "../algos/model";
import { signedRupees } from "../books/model";
import { LineChart } from "./LineChart";
import {
  anyGoing,
  buildBacktestBody,
  COMPARE_MAX,
  compareSeries,
  dayUrl,
  intraday,
  isSkipped,
  offeredDays,
  runCurve,
  runName,
  runState,
  togglePick,
  winRate,
  type RanDay,
} from "./model";

export interface BacktestsParams extends BaseParams {
  mode?: "runs" | "new" | "compare";
}

export const POLL_MS = 1000;
const tone = (n: number) => (n > 0 ? "good" : n < 0 ? "bad" : "");
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const time = (iso: string) => clock(iso, false);

export function Backtests({ params, setParams, publish }: WidgetProps<BacktestsParams>) {
  const { rest } = useApp();
  const mode = params.mode ?? "runs";
  const [runs, setRuns] = useState<BacktestRun[] | null>(null);
  const [off, setOff] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loads, setLoads] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [slots, setSlots] = useState<(string | null)[]>([]);

  useEffect(() => {
    let live = true;
    rest
      .get<BacktestRun[]>("/api/research/backtests")
      .then((r) => {
        if (!live) return;
        setRuns(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setOff(e.message);
        else setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [rest, loads]);

  // follow runs in progress: ask again each second until every run has finished
  const going = runs !== null && anyGoing(runs);
  useEffect(() => {
    if (!going) return;
    const t = setTimeout(() => setLoads((n) => n + 1), POLL_MS);
    return () => clearTimeout(t);
  }, [going, runs]);

  const refresh = useCallback(() => setLoads((n) => n + 1), []);
  const started = useCallback(
    (run: BacktestRun) => {
      setPicked(run.run_id);
      setParams({ mode: "runs" });
      refresh();
    },
    [setParams, refresh],
  );

  if (off) return <div className="empty-note">Backtests aren't available: {off}.</div>;
  const current = runs?.find((r) => r.run_id === picked) ?? null;
  return (
    <div className="book research">
      <div className="book-bar">
        {(["runs", "new", "compare"] as const).map((m) => (
          <button key={m} type="button" className={`book-tab${mode === m ? " on" : ""}`} aria-pressed={mode === m} onClick={() => setParams({ mode: m })}>
            {m === "runs" ? "Runs" : m === "new" ? "New" : "Compare"}
          </button>
        ))}
        <span className="book-spacer" />
        <button type="button" className="btn small" onClick={refresh}>
          Refresh
        </button>
      </div>
      <div className="book-scroll research-body">
        {error && (
          <div className="empty-note tone-bad" role="alert">
            {error}
          </div>
        )}
        {mode === "new" && <NewRun defaultInstrument={params.instrumentId ?? null} onStarted={started} />}
        {mode !== "new" && runs && (
          <RunList
            runs={runs}
            picked={picked}
            onPick={setPicked}
            compare={mode === "compare"}
            slots={slots}
            onToggle={(id) => setSlots((s) => togglePick(s, id))}
          />
        )}
        {mode === "runs" && current && <RunDetail run={current} publish={publish} onChanged={refresh} />}
        {mode === "compare" && runs && <Compare runs={runs} slots={slots} />}
      </div>
    </div>
  );
}

// --- the list of runs --------------------------------------------------------------------------

function RunList(props: { runs: BacktestRun[]; picked: string | null; onPick: (id: string) => void; compare: boolean; slots: (string | null)[]; onToggle: (id: string) => void }) {
  const { runs, picked, onPick, compare, slots, onToggle } = props;
  return (
    <>
      <div className="grid-head book-row bt-row" role="row">
        <span>{compare ? "Pick" : ""}</span>
        <span>Run</span>
        <span>State</span>
        <span className="num">Net</span>
        <span className="num">Days</span>
        <span>Started</span>
      </div>
      <div className="grid-body" role="grid" aria-label="Backtest runs">
        {!runs.length && <div className="empty-note">No backtests yet: start one from New.</div>}
        {runs.map((r) => {
          const st = runState(r);
          const slot = slots.indexOf(r.run_id);
          const full = slot < 0 && slots.filter(Boolean).length >= COMPARE_MAX;
          return (
            <div key={r.run_id} role="row" className={`row book-row bt-row${picked === r.run_id && !compare ? " selected" : ""}`} onClick={() => (compare ? r.summary && !full && onToggle(r.run_id) : onPick(r.run_id))}>
              <span>
                {compare && (
                  <input
                    type="checkbox"
                    aria-label={`Compare ${runName(r)}`}
                    checked={slot >= 0}
                    disabled={!r.summary || full}
                    title={!r.summary ? "Only finished runs can be compared" : full ? "Four runs at most" : ""}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => onToggle(r.run_id)}
                  />
                )}
                {compare && slot >= 0 && <i className={`swatch slot-${slot}`} aria-hidden="true" />}
              </span>
              <span title={r.run_id}>
                {runName(r)}
                {r.candle_based && (
                  <span className="algo-family" title={r.fills_note ?? "Ticks made from candles: fills are an estimate"}>
                    candles
                  </span>
                )}
              </span>
              <span className={`tone-${st.tone}`} title={st.text}>
                {st.text}
              </span>
              <span className={`num ${r.summary ? tone(r.summary.net_paise) : ""}`}>{r.summary ? signedRupees(r.summary.net_paise) : "–"}</span>
              <span className="num">{r.days}</span>
              <span className="dim" title={r.created_at}>
                {new Date(r.created_at).toLocaleDateString("en-CA").slice(5)} {time(r.created_at)}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}

// --- one run -----------------------------------------------------------------------------------

function RunDetail({ run, publish, onChanged }: { run: BacktestRun; publish: (id: string) => void; onChanged: () => void }) {
  const { rest } = useApp();
  const [stopping, setStopping] = useState(false);
  const [full, setFull] = useState<BacktestRun | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = `${run.run_id}|${run.state}|${run.progress}`; // read again as the run moves on

  useEffect(() => {
    let live = true;
    rest
      .get<BacktestRun>(`/api/research/backtests/${encodeURIComponent(run.run_id)}`)
      .then((r) => {
        if (!live) return;
        setFull(r);
        setError(null);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest, version, run.run_id]);

  useEffect(() => setDay(null), [run.run_id]);

  const s = run.spec;
  const summary = full?.run_id === run.run_id ? full.summary : undefined;
  const daily = full?.run_id === run.run_id ? (full.daily ?? []) : [];
  const shown = daily.find((d): d is RanDay => d.day === day && !isSkipped(d)) ?? null;
  const curve = useMemo(() => (full ? runCurve(full) : null), [full]);
  const dayCurve = useMemo(() => (shown ? intraday(shown) : null), [shown]);
  const params = Object.entries(s.params).map(([k, v]) => `${k} ${String(v)}`).join(" · ");
  return (
    <section className="bt-detail" aria-label={`Backtest ${run.run_id}`}>
      <h4>
        {runName(run)} <span className="dim">{run.run_id}</span>
      </h4>
      <div className="dim bt-spec">
        {s.algo} {s.side} {qty(s.qty)} {shortName(s.instrument_id)} {s.product} · from {s.start} · capital {rupees(s.capital_paise)}
        {params && ` · ${params}`}
      </div>
      {error && (
        <div className="empty-note tone-bad" role="alert">
          {error}
        </div>
      )}
      {run.state === "failed" && <div className="empty-note tone-bad">{run.error}</div>}
      {run.fills_note && <div className="empty-note dim">Candle-based: {run.fills_note}.</div>}
      {(run.state === "queued" || run.state === "running") && (
        <button
          type="button"
          className="btn small"
          disabled={stopping}
          onClick={() => {
            setStopping(true);
            rest
              .post<BacktestRun>(`/api/research/backtests/${encodeURIComponent(run.run_id)}/cancel`, {})
              .then(() => onChanged())
              .catch((e: unknown) => setError(errorText(e)))
              .finally(() => setStopping(false));
          }}
        >
          {stopping ? "Cancelling…" : "Cancel this run"}
        </button>
      )}
      {summary && (
        <div className="report-totals" aria-label="Run totals">
          <span>
            Net <b className={tone(summary.net_paise)}>{signedRupees(summary.net_paise)}</b>
          </span>
          <span>
            Gross <b className={tone(summary.gross_paise)}>{signedRupees(summary.gross_paise)}</b>
          </span>
          <span>
            Charges <b>{rupees(summary.charges_paise)}</b>
          </span>
          <span title="The deepest fall of the cumulative net from its high">
            Max drawdown <b className={summary.max_drawdown_paise ? "bad" : ""}>{rupees(summary.max_drawdown_paise)}</b>
          </span>
          <span>
            Winning days{" "}
            <b>
              {summary.winning_days} of {summary.days_run} ({winRate(summary)})
            </b>
          </span>
          <span>
            Trades <b>{summary.trades}</b>
          </span>
        </div>
      )}
      {curve && curve.days.length > 0 && <LineChart title="Cumulative net by day" xs={curve.days} series={curve.series} />}
      {daily.length > 0 && (
        <>
          <h4>Days</h4>
          <div className="grid-head book-row bt-day-row" role="row">
            <span>Day</span>
            <span className="num">Trades</span>
            <span className="num">Gross</span>
            <span className="num">Charges</span>
            <span className="num">Net</span>
            <span />
          </div>
          <div className="grid-body" role="grid" aria-label="Backtest days">
            {daily.map((d) =>
              isSkipped(d) ? (
                <div key={d.day} role="row" className="row book-row bt-day-row dim">
                  <span>{d.day}</span>
                  <span className="bt-skipped">skipped: {d.skipped}</span>
                </div>
              ) : (
                <div key={d.day} role="row" className={`row book-row bt-day-row${day === d.day ? " selected" : ""}`} onClick={() => setDay(day === d.day ? null : d.day)}>
                  <span>
                    {d.day}
                    {!d.algo_started && (
                      <span className="tone-bad" title="The day's ticks ended before the start time">
                        {" "}
                        (algo not started)
                      </span>
                    )}
                  </span>
                  <span className="num">{d.totals.trades}</span>
                  <span className={`num ${tone(d.totals.gross_paise)}`}>{signedRupees(d.totals.gross_paise)}</span>
                  <span className="num dim">{price(d.totals.charges_paise)}</span>
                  <span className={`num ${tone(d.totals.net_paise)}`}>{signedRupees(d.totals.net_paise)}</span>
                  <span className="bt-downloads" onClick={(e) => e.stopPropagation()}>
                    <a className="btn small" href={dayUrl(run.run_id, d.day, "csv")} download>
                      CSV
                    </a>
                    <a className="btn small" href={dayUrl(run.run_id, d.day, "pdf")} download>
                      PDF
                    </a>
                  </span>
                </div>
              ),
            )}
          </div>
        </>
      )}
      {shown && dayCurve && (
        <section aria-label={`Day ${shown.day}`}>
          <LineChart title={`${shown.day}: P&L before charges, through the day`} xs={dayCurve.times} series={dayCurve.series} xText={time} />
          <h4>Trades ({shown.trades.length})</h4>
          <div className="grid-body" role="grid" aria-label="Backtest trades">
            {shown.trades.map((x, n) => (
              <div key={n} role="row" className="row book-row report-trade-row" onClick={() => publish(x.instrument_id)}>
                <span className="dim">{time(x.ts)}</span>
                <span>{shortName(x.instrument_id)}</span>
                <span className={x.side === "BUY" ? "buy-text" : "sell-text"}>{x.side}</span>
                <span className="num">{qty(x.qty)}</span>
                <span className="num">{price(x.price_paise)}</span>
                <span className="dim">{x.strategy}</span>
              </div>
            ))}
          </div>
          {shown.rejects.length > 0 && (
            <ul className="report-notes" aria-label="Rejected">
              {shown.rejects.map((r, i) => (
                <li key={i} className="tone-bad">
                  {time(r.ts)} rejected: {r.detail}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </section>
  );
}

// --- compare -----------------------------------------------------------------------------------

function Compare({ runs, slots }: { runs: BacktestRun[]; slots: (string | null)[] }) {
  const picks = slots.flatMap((id, slot) => {
    const run = runs.find((r) => r.run_id === id && r.summary);
    return run ? [{ run, slot }] : [];
  });
  if (picks.length < 2) return <div className="empty-note">Tick two to four finished runs above to compare them.</div>;
  const chart = compareSeries(picks);
  const rows: [string, (r: BacktestRun) => string, ((r: BacktestRun) => number)?][] = [
    ["Net", (r) => signedRupees(r.summary!.net_paise), (r) => r.summary!.net_paise],
    ["Gross", (r) => signedRupees(r.summary!.gross_paise), (r) => r.summary!.gross_paise],
    ["Charges", (r) => rupees(r.summary!.charges_paise)],
    ["Max drawdown", (r) => rupees(r.summary!.max_drawdown_paise)],
    ["Winning days", (r) => `${r.summary!.winning_days} of ${r.summary!.days_run} (${winRate(r.summary!)})`],
    ["Trades", (r) => String(r.summary!.trades)],
    ["Days skipped", (r) => String(r.summary!.days_skipped.length)],
    ["Days", (r) => `${r.spec.days[0]} → ${r.spec.days[r.spec.days.length - 1]}`],
  ];
  return (
    <section className="bt-compare" aria-label="Compare runs">
      <LineChart title="Cumulative net by day" xs={chart.days} series={chart.series} />
      <table className="bt-compare-table">
        <thead>
          <tr>
            <th />
            {picks.map(({ run, slot }) => (
              <th key={run.run_id} scope="col">
                <i className={`swatch slot-${slot}`} aria-hidden="true" />
                {runName(run)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, text, sign]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              {picks.map(({ run }) => (
                <td key={run.run_id} className={`num ${sign ? tone(sign(run)) : ""}`}>
                  {text(run)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// --- a new run ---------------------------------------------------------------------------------

function NewRun({ defaultInstrument, onStarted }: { defaultInstrument: string | null; onStarted: (run: BacktestRun) => void }) {
  const { rest } = useApp();
  const [instrumentId, setInstrumentId] = useState<string | null>(defaultInstrument);
  const [form, setForm] = useState<BacktestForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [algo, setAlgo] = useState<string>("");
  const [values, setValues] = useState<FormValues>({});
  const [side, setSide] = useState<Side>("BUY");
  const [lots, setLots] = useState("1");
  const [product, setProduct] = useState("");
  const [candles, setCandles] = useState(false);
  const [days, setDays] = useState<string[]>([]);
  const [start, setStart] = useState("");
  const [capital, setCapital] = useState("");
  const [name, setName] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const instrument = useInstrument(instrumentId ?? undefined);
  const lotSize = instrument?.lot_size ?? 1;

  useEffect(() => {
    let live = true;
    const q = instrumentId ? `?instrument_id=${encodeURIComponent(instrumentId)}` : "";
    rest
      .get<BacktestForm>(`/api/research/backtests/form${q}`)
      .then((f) => {
        if (!live) return;
        setForm(f);
        setError(null);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest, instrumentId]);

  // the first form: the server's defaults and its first algo (kept when the contract changes)
  useEffect(() => {
    if (!form || algo) return;
    const first = form.algos[0];
    if (first) {
      setAlgo(first.code);
      setValues(initialValues(first));
    }
    setStart(form.defaults.start);
    setCapital(rupeeText(form.defaults.capital_paise));
    setProduct(form.defaults.product);
  }, [form, algo]);

  const offered = useMemo(() => offeredDays(form, candles), [form, candles]);
  // days no longer offered (another instrument's candles) are dropped
  useEffect(() => setDays((old) => (old.every((d) => offered.includes(d)) ? old : old.filter((d) => offered.includes(d)))), [offered]);

  if (error && !form) return <div className="empty-note tone-bad">{error}</div>;
  if (form && !form.algos.length) return <div className="empty-note">This server offers no algos to backtest.</div>;
  const spec = form?.algos.find((a) => a.code === algo);
  if (!form || !spec) return <div className="empty-note">loading…</div>; // until the defaults are in

  const pick = (code: string) => {
    const chosen = form.algos.find((a) => a.code === code);
    if (!chosen) return;
    setAlgo(code);
    setValues(initialValues(chosen));
    setProblems([]);
  };
  const toggleDay = (d: string) => setDays((old) => (old.includes(d) ? old.filter((x) => x !== d) : [...old, d]));

  const submit = () => {
    const { body, problems: found } = buildBacktestBody({ form, algo: spec.code, values, instrumentId, side, lots: Number(lots), lotSize, product, days, start, capital, name, candles });
    setProblems(found);
    if (!body) return;
    setBusy(true);
    rest
      .post<BacktestRun>("/api/research/backtests", body)
      .then(onStarted)
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };

  return (
    <form
      className="algo-form"
      aria-label="Start a backtest"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Algo</span>
          <select aria-label="Algo" value={spec.code} onChange={(e) => pick(e.target.value)}>
            {byFamily(form.algos).map(([family, list]) => (
              <optgroup key={family} label={familyLabel(family)}>
                {list.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <span className="algo-help dim">{spec.help}</span>
      </div>
      <div className="algo-form-row">
        <div className="algo-field wide">
          <span>Contract</span>
          {instrumentId ? (
            <span className="algo-picked">
              {shortName(instrumentId)}
              <button type="button" className="row-action" aria-label="Change contract" onClick={() => setInstrumentId(null)}>
                ×
              </button>
            </span>
          ) : (
            <SearchBox placeholder="Search product…" ariaLabel="Backtest contract" onPick={(i) => setInstrumentId(i.id)} />
          )}
        </div>
        <div className="algo-field">
          <span>Side</span>
          <div className="ticket-sides" role="group" aria-label="Side">
            {(["BUY", "SELL"] as Side[]).map((s) => (
              <button key={s} type="button" aria-pressed={side === s} className={`side ${s === "BUY" ? "buy" : "sell"}${side === s ? " on" : ""}`} onClick={() => setSide(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
        <div className="algo-field">
          <span>Lots</span>
          <div className="algo-control">
            <input aria-label="Lots" inputMode="numeric" value={lots} onChange={(e) => setLots(e.target.value)} size={4} />
            <span className="dim">= {qty((Number(lots) || 0) * lotSize)}</span>
          </div>
        </div>
        <div className="algo-field">
          <span>Product</span>
          <select aria-label="Product" value={product} onChange={(e) => setProduct(e.target.value)}>
            {form.products.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>
      </div>
      {spec.params.length > 0 && (
        <div className="algo-form-row">
          {spec.params.map((p) => (
            <ParamInput key={`${spec.code}.${p.name}`} param={p} value={values[p.name]} onChange={(v) => setValues((old) => ({ ...old, [p.name]: v }))} />
          ))}
        </div>
      )}
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Starts at (IST)</span>
          <input aria-label="Start time" value={start} onChange={(e) => setStart(e.target.value)} size={5} />
        </div>
        <div className="algo-field">
          <span>Capital</span>
          <div className="algo-control">
            <span className="dim">₹</span>
            <input aria-label="Capital" inputMode="decimal" value={capital} onChange={(e) => setCapital(e.target.value)} size={12} />
          </div>
        </div>
        <div className="algo-field wide">
          <span>Name (optional)</span>
          <input aria-label="Run name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>
      <fieldset className="bt-days">
        <legend>
          Days{" "}
          {form.candles && (
            <label title="Ticks made from imported candles (when nothing was recorded): an assumed path, so fills are an estimate">
              <input type="checkbox" checked={candles} onChange={(e) => setCandles(e.target.checked)} /> from candles
            </label>
          )}
          <button type="button" className="btn small" onClick={() => setDays(offered.slice(0, form.max_days))}>
            All
          </button>
          <button type="button" className="btn small" onClick={() => setDays([])}>
            None
          </button>
          <span className="dim">{days.length} picked</span>
        </legend>
        {!offered.length && <div className="empty-note">{candles ? (instrumentId ? "No candles imported for this contract." : "Pick a contract to see its candle days.") : "Nothing recorded yet."}</div>}
        <div className="bt-day-picks" role="group" aria-label="Days to run">
          {offered.map((d) => (
            <label key={d} className={days.includes(d) ? "on" : ""}>
              <input type="checkbox" checked={days.includes(d)} onChange={() => toggleDay(d)} />
              {d}
            </label>
          ))}
        </div>
      </fieldset>
      {problems.length > 0 && (
        <ul className="algo-problems" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="algo-form-row">
        <button type="submit" className={`ticket-send ${side === "BUY" ? "buy" : "sell"}`} disabled={busy}>
          Backtest {spec.name} {side} {qty((Number(lots) || 0) * lotSize)}
          {instrumentId ? ` ${shortName(instrumentId)}` : ""} over {days.length} day{days.length === 1 ? "" : "s"}
        </button>
      </div>
    </form>
  );
}

registerWidget<BacktestsParams>({
  kind: "backtests",
  title: "Backtests",
  description: "Run an algo over recorded days; results, equity curve, day reports (CSV/PDF), compare runs",
  component: Backtests,
  defaults: () => ({ link: "pink", mode: "runs" }),
  link: "drives",
});
