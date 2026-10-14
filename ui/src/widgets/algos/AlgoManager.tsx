// Algo manager: start, watch and control algos (docs/design/algos.md, K6.4).
//
//   [New algo ▾]  Pause  Resume  Stop                                         Stop all (2)
//   ▸ Algo   Type    Contract        Side  Qty   Filled  Avg        Working  State    Status
//   ▾ A0003  SPREAD  NIFTY26OCTFUT   BUY   150       75  25,010.00       75  Working  hedging 75…
//       Leg2 NIFTY26NOVFUT · Level 120.00 · Leg a filled 75 · Unhedged 0 · Legging max 4 ms
//       10:14:52.103 MT00012  BUY 75 NIFTY26OCTFUT @ 25,010.00  Filled
//
// The New form is drawn from the server's algo specs: every parameter becomes an input by its
// kind, so a new algo shows up here with no screen change. Starting and controlling pass the
// same trading gate as orders; the server checks everything again.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/rest";
import type { AlgoParamSpec, AlgoSpec, AlgoView, OrderView, Side } from "../../api/types";
import { useApp, useInstrument, useOrderSpec, useStore } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, price, qty, shortName } from "../../lib/format";
import { tradingBlock } from "../../state/gate";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { orderPrice, statusText, statusTone } from "../books/model";
import {
  buildAlgoBody,
  canPause,
  canResume,
  canStop,
  figures,
  initialValues,
  isActive,
  progress,
  sortAlgos,
  stateText,
  stateTone,
  type FormValues,
} from "./model";

export type AlgoParams = BaseParams;

type Action = "pause" | "resume" | "stop";

const EMPTY_ALGOS: Record<string, AlgoView> = {};
const EMPTY_ORDERS: Record<string, OrderView> = {};

const errorText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export function AlgoManager({ params, publish }: WidgetProps<AlgoParams>) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const algos = useStore((s) => (s.accountId ? s.books[s.accountId]?.algos : undefined)) ?? EMPTY_ALGOS;
  const orders = useStore((s) => (s.accountId ? s.books[s.accountId]?.orders : undefined)) ?? EMPTY_ORDERS;
  const rows = useMemo(() => sortAlgos(algos), [algos]);
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [starting, setStarting] = useState(false);
  const [specs, setSpecs] = useState<AlgoSpec[] | null>(null);
  const [specError, setSpecError] = useState("");

  useEffect(() => {
    setSpecs(null);
    setSpecError("");
    if (!accountId) return;
    let live = true;
    rest
      .get<AlgoSpec[]>(`/api/accounts/${encodeURIComponent(accountId)}/algo-specs`)
      .then((s) => live && setSpecs(s))
      .catch((e: unknown) => live && setSpecError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest, accountId]);

  const control = useCallback(
    (list: AlgoView[], action: Action) => {
      if (!accountId) return;
      if (action !== "stop") {
        // stopping is getting out: like a cancel, it stays allowed while trading is stopped
        const why = tradingBlock(store.getState(), accountId);
        if (why) {
          store.addMessage("WARN", "", `Not done: ${why}`);
          return;
        }
      }
      for (const a of list) {
        rest
          .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos/${encodeURIComponent(a.algo_id)}/${action}`, {})
          .then((view) => store.applyAlgos(accountId, [view]))
          .catch((e: unknown) => store.addMessage("ERROR", a.instrument_id, `${action} ${a.algo_id} refused: ${errorText(e)}`));
      }
    },
    [rest, store, accountId],
  );

  const current = selected ? algos[selected] : undefined;
  const stoppable = useMemo(() => rows.filter(canStop), [rows]);

  return (
    <div className="book algos">
      <div className="book-bar">
        <button
          type="button"
          className={`btn small${starting ? " on" : ""}`}
          aria-expanded={starting}
          disabled={!accountId}
          onClick={() => setStarting((v) => !v)}
        >
          New algo {starting ? "▴" : "▾"}
        </button>
        <button type="button" className="btn small" disabled={!current || !canPause(current)} onClick={() => current && control([current], "pause")}>
          Pause
        </button>
        <button type="button" className="btn small" disabled={!current || !canResume(current)} onClick={() => current && control([current], "resume")}>
          Resume
        </button>
        <button type="button" className="btn small" disabled={!current || !canStop(current)} onClick={() => current && control([current], "stop")}>
          Stop
        </button>
        <span className="book-spacer" />
        <button
          type="button"
          className="btn small danger"
          disabled={!stoppable.length}
          title="Stop every running algo on this account (their quotes are pulled; hedges keep working)"
          onClick={() => control(stoppable, "stop")}
        >
          Stop all{stoppable.length ? ` (${stoppable.length})` : ""}
        </button>
      </div>
      {starting && accountId && specError && <div className="algo-form empty-note">Algos unavailable: {specError}</div>}
      {starting && accountId && !specError && !specs && <div className="algo-form empty-note">Loading algos…</div>}
      {starting && accountId && specs && !specs.length && <div className="algo-form empty-note">This account offers no algos.</div>}
      {starting && accountId && specs && specs.length > 0 && (
        <StartForm
          key={accountId}
          accountId={accountId}
          specs={specs}
          defaultInstrument={params.instrumentId ?? current?.instrument_id ?? null}
          onStarted={(view) => {
            store.applyAlgos(accountId, [view]);
            setSelected(view.algo_id);
            setStarting(false);
          }}
          onCancel={() => setStarting(false)}
        />
      )}
      <div className="book-scroll">
        <div className="grid-head book-row algos-row" role="row">
          <span />
          <span>Algo</span>
          <span>Type</span>
          <span>Contract</span>
          <span>Side</span>
          <span className="num">Qty</span>
          <span className="num">Filled</span>
          <span className="num">Avg</span>
          <span className="num">Working</span>
          <span>State</span>
          <span>Status</span>
        </div>
        <div className="grid-body" role="grid" aria-label="Algos">
          {!accountId && <div className="empty-note">No account selected.</div>}
          {accountId && !rows.length && <div className="empty-note">No algos today. New algo ▾ starts one.</div>}
          {rows.map((a) => {
            const expanded = !!open[a.algo_id];
            return (
              <div key={a.algo_id} className="algo-group">
                <div
                  role="row"
                  aria-selected={selected === a.algo_id}
                  aria-expanded={expanded}
                  className={`row book-row algos-row${selected === a.algo_id ? " selected" : ""}${isActive(a) ? "" : " finished"}`}
                  title={`${a.algo_id} · started ${clock(a.created_at)} by ${a.user_id}`}
                  onClick={() => {
                    setSelected(a.algo_id);
                    publish(a.instrument_id);
                  }}
                  onDoubleClick={() => setOpen((o) => ({ ...o, [a.algo_id]: !expanded }))}
                >
                  <button
                    type="button"
                    className="row-action"
                    aria-label={`${expanded ? "Hide" : "Show"} ${a.algo_id} details`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpen((o) => ({ ...o, [a.algo_id]: !expanded }));
                    }}
                    onDoubleClick={(e) => e.stopPropagation()}
                  >
                    {expanded ? "▾" : "▸"}
                  </button>
                  <span>{a.algo_id}</span>
                  <span>{a.code}</span>
                  <span>{shortName(a.instrument_id)}</span>
                  <span className={a.side === "BUY" ? "buy-text" : "sell-text"}>{a.side}</span>
                  <span className="num">{qty(a.qty)}</span>
                  <span className="num algo-filled" style={{ ["--done" as string]: `${Math.round(progress(a) * 100)}%` }}>
                    {qty(a.filled_qty)}
                  </span>
                  <span className="num">{a.filled_qty > 0 ? price(a.avg_price_paise) : "–"}</span>
                  <span className="num">{qty(a.working_qty)}</span>
                  <span className={`tone-${stateTone(a)}`}>{stateText(a.state)}</span>
                  <span className="algo-status" title={a.reason || a.status}>
                    {a.reason && !isActive(a) ? a.reason : a.status}
                  </span>
                </div>
                {expanded && <AlgoDetail algo={a} orders={orders} spec={specs?.find((x) => x.code === a.code)} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Under an expanded row: the algo's own figures, its settings and its child orders. */
function AlgoDetail({ algo, orders, spec }: { algo: AlgoView; orders: Record<string, OrderView>; spec: AlgoSpec | undefined }) {
  const children = algo.children.map((id) => orders[id]).filter((o): o is OrderView => !!o);
  const labels = Object.fromEntries((spec?.params ?? []).map((p) => [p.name, p.label]));
  const facts = figures(algo.extra, labels);
  // the settings it was started with (those not already among its figures), labelled by its spec
  const settings = figures(Object.fromEntries(Object.entries(algo.params).filter(([k]) => !(k in algo.extra))), labels);
  return (
    <div className="algo-detail" role="region" aria-label={`${algo.algo_id} details`}>
      {facts.length > 0 && (
        <div className="algo-facts">
          {facts.map((f) => (
            <span key={f.label} className={f.alert ? "tone-bad" : undefined}>
              <span className="dim">{f.label}</span> {f.text}
            </span>
          ))}
        </div>
      )}
      {settings.length > 0 && (
        <div className="algo-facts dim" aria-label="Settings">
          {settings.map((f) => (
            <span key={f.label}>
              {f.label} {f.text}
            </span>
          ))}
        </div>
      )}
      {children.length === 0 ? (
        <div className="empty-note">No child orders yet.</div>
      ) : (
        children.map((o) => (
          <div key={o.request.client_order_id} className="algo-child" title={o.reason || undefined}>
            <span className="dim">{clock(o.created_at)}</span>
            <span>{o.request.client_order_id}</span>
            <span className={o.request.side === "BUY" ? "buy-text" : "sell-text"}>{o.request.side}</span>
            <span className="num">{qty(o.request.qty)}</span>
            <span>{shortName(o.request.instrument_id)}</span>
            <span className="num">{orderPrice(o)}</span>
            <span className={`tone-${statusTone(o)}`}>
              {statusText(o)}
              {o.filled_qty > 0 && o.status !== "FILLED" ? ` ${qty(o.filled_qty)}` : ""}
            </span>
          </div>
        ))
      )}
      {children.length < algo.children.length && <div className="empty-note">{algo.children.length - children.length} older orders not loaded.</div>}
    </div>
  );
}

interface StartFormProps {
  accountId: string;
  specs: AlgoSpec[]; // at least one
  defaultInstrument: string | null;
  onStarted: (view: AlgoView) => void;
  onCancel: () => void;
}

/** The start form, drawn from the chosen algo's spec. */
function StartForm({ accountId, specs, defaultInstrument, onStarted, onCancel }: StartFormProps) {
  const { rest, store } = useApp();
  const [code, setCode] = useState<string>(specs[0]!.code);
  const spec = specs.find((s) => s.code === code) ?? specs[0]!;
  const [values, setValues] = useState<FormValues>(() => initialValues(spec));
  const [instrumentId, setInstrumentId] = useState<string | null>(defaultInstrument);
  const [side, setSide] = useState<Side>("BUY");
  const [lots, setLots] = useState("1");
  const [product, setProduct] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const instrument = useInstrument(instrumentId ?? undefined);
  const orderSpec = useOrderSpec(accountId, instrumentId ?? undefined);
  const products = orderSpec?.products ?? [];
  const chosenProduct = products.includes(product) ? product : (products[0] ?? "");
  const lotSize = orderSpec?.lot_size ?? instrument?.lot_size ?? 1;

  const pick = (next: string) => {
    const chosen = specs.find((s) => s.code === next);
    if (!chosen) return;
    setCode(next);
    setValues(initialValues(chosen)); // each algo starts from its own defaults
    setProblems([]);
  };

  const set = (name: string, v: string | boolean) => setValues((old) => ({ ...old, [name]: v }));

  const submit = () => {
    const why = tradingBlock(store.getState(), accountId);
    if (why) {
      setProblems([why]);
      return;
    }
    const { body, problems: found } = buildAlgoBody({ spec, values, instrumentId, side, lots: Number(lots), lotSize, product: chosenProduct });
    setProblems(found);
    if (!body) return;
    setBusy(true);
    rest
      .post<AlgoView>(`/api/accounts/${encodeURIComponent(accountId)}/algos`, body)
      .then((view) => onStarted(view))
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };

  return (
    <form
      className="algo-form"
      aria-label="Start an algo"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Algo</span>
          <select aria-label="Algo" value={spec.code} onChange={(e) => pick(e.target.value)}>
            {specs.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
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
            <SearchBox placeholder="Search product…" ariaLabel="Algo contract" onPick={(i) => setInstrumentId(i.id)} />
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
        {products.length > 0 && (
          <div className="algo-field">
            <span>Product</span>
            <select aria-label="Product" value={chosenProduct} onChange={(e) => setProduct(e.target.value)}>
              {products.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </div>
        )}
      </div>
      {spec.params.length > 0 && (
        <div className="algo-form-row">
          {spec.params.map((p) => (
            <ParamInput key={`${spec.code}.${p.name}`} param={p} value={values[p.name]} onChange={(v) => set(p.name, v)} />
          ))}
        </div>
      )}
      {problems.length > 0 && (
        <ul className="algo-problems" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="algo-form-row">
        <button type="submit" className={`ticket-send ${side === "BUY" ? "buy" : "sell"}`} disabled={busy}>
          Start {spec.name} {side} {qty((Number(lots) || 0) * lotSize)}
          {instrumentId ? ` ${shortName(instrumentId)}` : ""}
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Close
        </button>
      </div>
    </form>
  );
}

const UNITS: Record<string, string> = { seconds: "s", minutes: "min", percent: "%", ticks: "ticks" };

/** One parameter's input, chosen by its kind. An unknown kind gets a plain text box. */
function ParamInput({ param: p, value, onChange }: { param: AlgoParamSpec; value: string | boolean | undefined; onChange: (v: string | boolean) => void }) {
  const label = (
    <span>
      {p.label}
      {p.required ? "" : " (optional)"}
    </span>
  );
  if (p.kind === "flag") {
    return (
      <label className="algo-field" title={p.help}>
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }
  const text = typeof value === "string" ? value : "";
  if (p.kind === "choice") {
    return (
      <div className="algo-field" title={p.help}>
        {label}
        <select aria-label={p.label} value={text} onChange={(e) => onChange(e.target.value)}>
          {!p.required && <option value="">–</option>}
          {p.choices.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
    );
  }
  if (p.kind === "instrument") {
    return (
      <div className="algo-field wide" title={p.help}>
        {label}
        {text ? (
          <span className="algo-picked">
            {shortName(text)}
            <button type="button" className="row-action" aria-label={`Change ${p.label}`} onClick={() => onChange("")}>
              ×
            </button>
          </span>
        ) : (
          <SearchBox placeholder="Search product…" ariaLabel={p.label} onPick={(i) => onChange(i.id)} />
        )}
      </div>
    );
  }
  const priced = p.kind === "price" || p.kind === "price_offset";
  const unit = priced ? "₹" : (UNITS[p.kind] ?? "");
  return (
    <div className="algo-field" title={p.help}>
      {label}
      <div className="algo-control">
        {priced && <span className="dim">{unit}</span>}
        <input
          aria-label={p.label}
          inputMode={priced || p.kind === "seconds" || p.kind === "minutes" || p.kind === "percent" ? "decimal" : "numeric"}
          value={text}
          placeholder={p.required ? "" : "–"}
          size={priced ? 9 : 4}
          onChange={(e) => onChange(e.target.value)}
        />
        {unit && !priced && <span className="dim">{unit}</span>}
      </div>
    </div>
  );
}

registerWidget<AlgoParams>({
  kind: "algos",
  title: "Algos",
  description: "Start, watch, pause and stop algos (slicer, spreader…); child orders per algo",
  component: AlgoManager,
  defaults: () => ({ link: "pink" }),
  link: "drives",
});
