// GTT book: orders the BROKER holds until a price is touched (docs/design/gtt.md, K9.3/K9.4).
//
//   [New GTT ▾]  Delete                                                     GTTs held by the broker
//   GTT   Contract        Kind   Legs                                          Status     Updated
//   G12   NIFTY26OCTFUT   OCO    SELL 75 @ 24,759.40 when 24,759.90 · SELL …   Active     10:14:52
//
// The form is drawn from the server's GTT spec (kinds, legs per kind, labels, help, tick, lot):
// nothing here knows which kinds exist. A broker without GTTs answers 404 and the book says so.

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/rest";
import type { GttSpec, GttView, Side } from "../../api/types";
import { useApp, useInstrument, useStore, useTick } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, qty, shortName } from "../../lib/format";
import { tradingBlock } from "../../state/gate";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { buildGttBody, defaultLegs, gttTone, legsText, sortGtts, type LegForm } from "./model";

export type GttParams = BaseParams;

const EMPTY: Record<string, GttView> = {};
const errorText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

/** The broker's GTT spec for one contract; `unsupported` when the broker holds no GTTs. */
export function useGttSpec(accountId: string | null, instrumentId: string | null | undefined) {
  const { rest } = useApp();
  const [state, setState] = useState<{ spec: GttSpec | null; error: string }>({ spec: null, error: "" });
  useEffect(() => {
    setState({ spec: null, error: "" });
    if (!accountId || !instrumentId) return;
    let live = true;
    rest
      .get<GttSpec>(`/api/accounts/${encodeURIComponent(accountId)}/gtt-spec?instrument_id=${encodeURIComponent(instrumentId)}`)
      .then((spec) => live && setState({ spec, error: "" }))
      .catch((e: unknown) => live && setState({ spec: null, error: errorText(e) }));
    return () => {
      live = false;
    };
  }, [rest, accountId, instrumentId]);
  return state;
}

export function GttBook({ params, publish }: WidgetProps<GttParams>) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const gtts = useStore((s) => (s.accountId ? s.books[s.accountId]?.gtts : undefined)) ?? EMPTY;
  const rows = useMemo(() => sortGtts(gtts), [gtts]);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const current = selected ? gtts[selected] : undefined;

  const remove = (g: GttView) => {
    if (!accountId) return;
    rest
      .delete<GttView>(`/api/accounts/${encodeURIComponent(accountId)}/gtt/${encodeURIComponent(g.gtt_id)}`)
      .then((view) => store.applyGtts(accountId, [view]))
      .catch((e: unknown) => store.addMessage("ERROR", g.instrument_id, `Delete GTT ${g.gtt_id} refused: ${errorText(e)}`));
  };

  return (
    <div className="book gtts">
      <div className="book-bar">
        <button type="button" className={`btn small${creating ? " on" : ""}`} aria-expanded={creating} disabled={!accountId} onClick={() => setCreating((v) => !v)}>
          New GTT {creating ? "▴" : "▾"}
        </button>
        <button type="button" className="btn small" disabled={!current || current.status !== "active"} onClick={() => current && remove(current)}>
          Delete
        </button>
        <span className="book-spacer" />
        <span className="dim" title="A GTT is kept by the broker: it works even while MockTrade is down">
          Held by the broker
        </span>
      </div>
      {creating && accountId && (
        <GttForm
          accountId={accountId}
          defaultInstrument={params.instrumentId ?? current?.instrument_id ?? null}
          onPlaced={(view) => {
            store.applyGtts(accountId, [view]);
            setSelected(view.gtt_id);
            setCreating(false);
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      <div className="book-scroll">
        <div className="grid-head book-row gtts-row" role="row">
          <span>GTT</span>
          <span>Contract</span>
          <span>Kind</span>
          <span>Legs</span>
          <span>Status</span>
          <span>Updated</span>
        </div>
        <div className="grid-body" role="grid" aria-label="GTTs">
          {!accountId && <div className="empty-note">No account selected.</div>}
          {accountId && !rows.length && <div className="empty-note">No GTTs. New GTT ▾ places one with the broker.</div>}
          {rows.map((g) => (
            <div
              key={g.gtt_id}
              role="row"
              aria-selected={selected === g.gtt_id}
              className={`row book-row gtts-row${selected === g.gtt_id ? " selected" : ""}${g.status === "active" ? "" : " finished"}`}
              title={g.reason || (g.order_id ? `placed order ${g.order_id}` : undefined)}
              onClick={() => {
                setSelected(g.gtt_id);
                publish(g.instrument_id);
              }}
            >
              <span>{g.gtt_id}</span>
              <span>{shortName(g.instrument_id)}</span>
              <span>{g.kind === "single" ? "Single" : "OCO"}</span>
              <span className="gtt-legs">{legsText(g)}</span>
              <span className={`tone-${gttTone(g)}`}>
                {g.status.charAt(0).toUpperCase() + g.status.slice(1)}
                {g.fired_leg !== null && g.order_id ? ` · leg ${g.fired_leg + 1}` : ""}
              </span>
              <span className="dim">{g.updated_at ? clock(g.updated_at) : ""}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface GttFormProps {
  accountId: string;
  defaultInstrument: string | null;
  defaultSide?: Side;
  defaultLots?: number;
  onPlaced: (view: GttView) => void;
  onCancel: () => void;
}

/** A new GTT, drawn from the broker's GTT spec for the chosen contract. */
export function GttForm({ accountId, defaultInstrument, defaultSide = "SELL", defaultLots = 1, onPlaced, onCancel }: GttFormProps) {
  const [instrumentId, setInstrumentId] = useState<string | null>(defaultInstrument);
  const { spec, error } = useGttSpec(accountId, instrumentId);
  return (
    <div className="algo-form" role="form" aria-label="Place a GTT">
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
            <SearchBox placeholder="Search product…" ariaLabel="GTT contract" onPick={(i) => setInstrumentId(i.id)} />
          )}
        </div>
      </div>
      {instrumentId && error && <div className="empty-note">GTTs unavailable: {error}</div>}
      {instrumentId && !error && !spec && <div className="empty-note">Loading what the broker allows…</div>}
      {spec && (
        <GttLegsForm key={spec.instrument_id} accountId={accountId} spec={spec} defaultSide={defaultSide} defaultLots={defaultLots} onPlaced={onPlaced} onCancel={onCancel} />
      )}
      {!spec && (
        <div className="algo-form-row">
          <button type="button" className="btn small" onClick={onCancel}>
            Close
          </button>
        </div>
      )}
    </div>
  );
}

function GttLegsForm({ accountId, spec, defaultSide, defaultLots, onPlaced, onCancel }: { accountId: string; spec: GttSpec; defaultSide: Side; defaultLots: number } & Pick<GttFormProps, "onPlaced" | "onCancel">) {
  const { rest, store } = useApp();
  const instrument = useInstrument(spec.instrument_id);
  const tick = useTick(spec.instrument_id);
  const ltp = tick?.ltp_paise ?? instrument?.ltp_paise ?? null;
  const [kind, setKind] = useState(spec.kinds[0] ?? "");
  const [side, setSide] = useState<Side>(defaultSide);
  const [lots, setLots] = useState(String(defaultLots));
  const [product, setProduct] = useState(spec.products[0] ?? "");
  const [legs, setLegs] = useState<LegForm[]>(() => defaultLegs(spec, kind, ltp, side));
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  // start from the market once its price is known, and again when the kind or side changes
  const restart = (k: string, s: Side) => setLegs(defaultLegs(spec, k, ltp, s));
  useEffect(() => {
    if (ltp === null) return;
    // decided on the newest legs, not this render's: what the trader typed is never overwritten
    setLegs((old) => (old.every((l) => l.trigger === "") ? defaultLegs(spec, kind, ltp, side) : old));
  }, [ltp, spec, kind, side]);

  const setLeg = (i: number, field: "trigger" | "price", value: string) => setLegs((old) => old.map((l, n) => (n === i ? { ...l, [field]: value } : l)));

  const submit = () => {
    const why = tradingBlock(store.getState(), accountId);
    if (why) return setProblems([why]);
    const { body, problems: found } = buildGttBody({ spec, kind, legs, lots: Number(lots), product, ltp });
    setProblems(found);
    if (!body) return;
    setBusy(true);
    rest
      .post<GttView>(`/api/accounts/${encodeURIComponent(accountId)}/gtt`, body)
      .then(onPlaced)
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };

  const legName = (i: number) => (legs.length === 1 ? "" : i === 0 ? "Stop (lower)" : "Target (upper)");
  return (
    <>
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Kind</span>
          <select
            aria-label="GTT kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              restart(e.target.value, side);
            }}
          >
            {spec.kinds.map((k) => (
              <option key={k} value={k}>
                {spec.labels[k] ?? k}
              </option>
            ))}
          </select>
        </div>
        <span className="algo-help dim">{spec.help[kind]}</span>
      </div>
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Side</span>
          <div className="ticket-sides" role="group" aria-label="GTT side">
            {(["BUY", "SELL"] as Side[]).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={side === s}
                className={`side ${s === "BUY" ? "buy" : "sell"}${side === s ? " on" : ""}`}
                onClick={() => {
                  setSide(s);
                  restart(kind, s);
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
        <div className="algo-field">
          <span>Lots</span>
          <div className="algo-control">
            <input aria-label="GTT lots" inputMode="numeric" value={lots} size={4} onChange={(e) => setLots(e.target.value)} />
            <span className="dim">= {qty((Number(lots) || 0) * spec.lot_size)}</span>
          </div>
        </div>
        <div className="algo-field">
          <span>Product</span>
          <select aria-label="GTT product" value={product} onChange={(e) => setProduct(e.target.value)}>
            {spec.products.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>
      </div>
      {legs.map((leg, i) => (
        <div key={i} className="algo-form-row" aria-label={legName(i) || "GTT leg"}>
          {legName(i) && <span className="dim gtt-leg-name">{legName(i)}</span>}
          <div className="algo-field">
            <span>Trigger</span>
            <div className="algo-control">
              <span className="dim">₹</span>
              <input aria-label={`${legName(i) || "GTT"} trigger`} inputMode="decimal" size={9} value={leg.trigger} onChange={(e) => setLeg(i, "trigger", e.target.value)} />
            </div>
          </div>
          <div className="algo-field">
            <span>Limit price</span>
            <div className="algo-control">
              <span className="dim">₹</span>
              <input aria-label={`${legName(i) || "GTT"} price`} inputMode="decimal" size={9} value={leg.price} onChange={(e) => setLeg(i, "price", e.target.value)} />
            </div>
          </div>
        </div>
      ))}
      {problems.length > 0 && (
        <ul className="algo-problems" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="algo-form-row">
        <button type="button" className={`ticket-send ${side === "BUY" ? "buy" : "sell"}`} disabled={busy} onClick={submit}>
          Place GTT {side} {qty((Number(lots) || 0) * spec.lot_size)} {shortName(spec.instrument_id)}
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Close
        </button>
        <span className="dim">Held by the broker: it works even if MockTrade is down. Expires after {spec.expiry_days} days.</span>
      </div>
    </>
  );
}

registerWidget<GttParams>({
  kind: "gtt",
  title: "GTT Book",
  description: "Good-till-triggered orders held by the broker: place, watch, delete",
  component: GttBook,
  defaults: () => ({ link: "pink" }),
  link: "drives",
});
