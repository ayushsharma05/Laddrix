// The Autospreader (#414): define spreads of 2 to 6 legs and see each one's live implied
// price. The server keeps the definitions (only yours) and prices each spread from its legs:
// it streams as the instrument "SPREAD:<id>", like any contract.
//
//   [Spreads (2)] [New]                                            2 of 50
//   NIFTY Nov/Dec   +1 NIFTY26NOVFUT −1 NIFTY26DECFUT   bid −152.00 (10)  ask −149.00 (2)  Ladder Edit
//
//   New / Edit:   Name [NIFTY Nov/Dec]
//                 Leg  Contract         Ratio  Price ×  Quote  Pay-up
//                 1    NIFTY26NOVFUT     +1      +1       ☑      2      ×
//                 2    NIFTY26DECFUT     −1      −1       ☐      2      ×   (one or more quote)
//                 + Add leg                       price = NIFTY26NOVFUT − NIFTY26DECFUT
//                 Controls: Re-quote threshold [1] ticks  Max unhedged [0]  Chase [0] ticks
//                           Hedge timeout [5] s           (drawn from GET /api/spreads/spec)
//                 [Save] [Cancel]           (problems, in the server's words)

import { useEffect, useState } from "react";
import type { AlgoParamSpec, Spread, SpreadSpec } from "../../api/types";
import { useApp, useTick } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { price, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { ParamInput } from "../algos/AlgoManager";
import { addLeg, bodyOf, controlsBody, controlText, draftOf, emptyDraft, formula, legSummary, MAX_LEGS, missing, quoteLeg, removeLeg, setLeg, type Draft } from "./model";

export interface AutospreaderParams extends BaseParams {
  mode?: "list" | "edit";
  spreadId?: string; // the spread being edited ("edit" without one: a new spread)
}

const MAX_SPREADS = 50; // the server's limit per user (it says so too)
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function Autospreader({ params, setParams, open }: WidgetProps<AutospreaderParams>) {
  const { rest } = useApp();
  const [spreads, setSpreads] = useState<Spread[] | null>(null);
  const [controls, setControls] = useState<AlgoParamSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const mode = params.mode ?? "list";

  useEffect(() => {
    let live = true;
    rest
      .get<Spread[]>("/api/spreads")
      .then((list) => live && setSpreads(list))
      .catch((e: unknown) => live && setError(errorText(e)));
    rest
      .get<SpreadSpec>("/api/spreads/spec")
      .then((spec) => live && setControls(spec.controls))
      .catch(() => undefined); // without it the form sends no controls: the server keeps them
    return () => {
      live = false;
    };
  }, [rest]);

  if (error && !spreads) return <div className="empty-note tone-bad">{error}</div>;
  if (!spreads) return <div className="empty-note">loading…</div>;
  const editing = params.spreadId ? spreads.find((s) => s.spread_id === params.spreadId) : undefined;
  return (
    <div className="book ase">
      <div className="book-bar">
        <button type="button" className={`book-tab${mode === "list" ? " on" : ""}`} aria-pressed={mode === "list"} onClick={() => setParams({ mode: "list", spreadId: undefined })}>
          Spreads ({spreads.length})
        </button>
        <button type="button" className={`book-tab${mode === "edit" && !editing ? " on" : ""}`} aria-pressed={mode === "edit" && !editing} onClick={() => setParams({ mode: "edit", spreadId: undefined })}>
          New
        </button>
        {editing && mode === "edit" && <span className="book-tab on">Edit: {editing.name}</span>}
        <span className="book-spacer" />
        <span className="dim">
          {spreads.length} of {MAX_SPREADS}
        </span>
      </div>
      <div className="book-scroll ase-body">
        {mode === "list" && (
          <SpreadList
            spreads={spreads}
            onLadder={(s) => open("ladder", { instrumentId: s.instrument_id, link: "none" })}
            onEdit={(s) => setParams({ mode: "edit", spreadId: s.spread_id })}
            onGone={(id) => setSpreads((l) => (l ?? []).filter((s) => s.spread_id !== id))}
          />
        )}
        {mode === "edit" && (
          <SpreadForm
            key={editing?.spread_id ?? "new"}
            spread={editing}
            controls={controls}
            onSaved={(s) => {
              setSpreads((l) => [...(l ?? []).filter((x) => x.spread_id !== s.spread_id), s].sort((a, b) => a.name.localeCompare(b.name)));
              setParams({ mode: "list", spreadId: undefined });
            }}
            onCancel={() => setParams({ mode: "list", spreadId: undefined })}
          />
        )}
      </div>
    </div>
  );
}

function SpreadList({ spreads, onLadder, onEdit, onGone }: { spreads: Spread[]; onLadder: (s: Spread) => void; onEdit: (s: Spread) => void; onGone: (id: string) => void }) {
  const { rest } = useApp();
  const [armed, setArmed] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const remove = (id: string) =>
    rest
      .delete(`/api/spreads/${id}`)
      .then(() => onGone(id))
      .catch((e: unknown) => setProblem(errorText(e)))
      .finally(() => setArmed(null));
  return (
    <>
      {problem && (
        <div className="empty-note tone-bad" role="alert">
          {problem}
        </div>
      )}
      <div className="grid-head book-row ase-row" role="row">
        <span>Spread</span>
        <span>Legs</span>
        <span className="num">Bid</span>
        <span className="num">Ask</span>
        <span />
      </div>
      <div className="grid-body" role="grid" aria-label="Spreads">
        {!spreads.length && <div className="empty-note">No spreads yet: make one from New.</div>}
        {spreads.map((s) => (
          <div key={s.spread_id} role="row" className="row book-row ase-row" title={`price = ${formula(s.legs, shortName)}`}>
            <span className="strong">{s.name}</span>
            <span className="dim">{legSummary(s.legs, shortName)}</span>
            <LivePrice spread={s} />
            <span className="alert-actions">
              <button type="button" className="btn small" title="Trade it: a ladder of its implied prices" onClick={() => onLadder(s)}>
                Ladder
              </button>
              <button type="button" className="btn small" onClick={() => onEdit(s)}>
                Edit
              </button>
              {armed === s.spread_id ? (
                <button type="button" className="btn small danger" onClick={() => void remove(s.spread_id)}>
                  Confirm delete
                </button>
              ) : (
                <button type="button" className="btn small" onClick={() => setArmed(s.spread_id)}>
                  Delete…
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

/** The spread's implied bid and ask now, with how many spread lots each has. */
function LivePrice({ spread }: { spread: Spread }) {
  const tick = useTick(spread.instrument_id);
  const bid = tick?.bids[0];
  const ask = tick?.asks[0];
  const side = (level: typeof bid, label: string) => (
    <span className={`num ${label}-text`} aria-label={`${spread.name} ${label === "buy" ? "bid" : "ask"}`}>
      {level ? `${price(level[0])} (${level[1]})` : "–"}
    </span>
  );
  return (
    <>
      {side(bid, "buy")}
      {side(ask, "sell")}
    </>
  );
}

function SpreadForm({ spread, controls, onSaved, onCancel }: { spread?: Spread; controls: AlgoParamSpec[]; onSaved: (s: Spread) => void; onCancel: () => void }) {
  const { rest } = useApp();
  const [draft, setDraft] = useState<Draft>(() => (spread ? draftOf(spread) : emptyDraft()));
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const save = () => {
    const tuned = controlsBody(controls, draft);
    const first = [...missing(draft), ...tuned.problems];
    setProblems(first);
    if (first.length) return;
    setBusy(true);
    const body = controls.length ? { ...bodyOf(draft), controls: tuned.controls } : bodyOf(draft);
    (spread ? rest.put<Spread>(`/api/spreads/${spread.spread_id}`, body) : rest.post<Spread>("/api/spreads", body))
      .then(onSaved)
      .catch((e: unknown) => setProblems(errorText(e).split("; ")))
      .finally(() => setBusy(false));
  };
  const legs = bodyOf(draft).legs.filter((l) => l.instrument_id);
  return (
    <form
      className="algo-form ase-form"
      aria-label={spread ? `Edit ${spread.name}` : "New spread"}
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <label className="algo-form-row">
        <span>Name</span>
        <input value={draft.name} maxLength={40} aria-label="Spread name" placeholder="e.g. NIFTY Nov/Dec" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
      </label>
      <div className="grid-head ase-leg" role="row">
        <span>Leg</span>
        <span>Contract</span>
        <span title="Lots of this leg per spread lot: + is bought when you buy the spread, − is sold">Ratio</span>
        <span title="How much this leg's price counts in the spread's price (usually the ratio)">Price ×</span>
        <span title="Legs that rest a quote (one or more); a fill on any is hedged at once on every other leg">Quote</span>
        <span title="How many ticks worse than the best price this leg's hedge may pay">Pay-up</span>
        <span />
      </div>
      {draft.legs.map((leg, i) => (
        <div key={i} className="ase-leg" role="group" aria-label={`Leg ${i + 1}`}>
          <span className="dim">{i + 1}</span>
          {leg.instrumentId ? (
            <button type="button" className="btn small ase-contract" title="Pick another contract" onClick={() => setDraft(setLeg(draft, i, { instrumentId: "" }))}>
              {shortName(leg.instrumentId)}
            </button>
          ) : (
            <SearchBox placeholder="Contract…" ariaLabel={`Leg ${i + 1} contract`} onPick={(found) => setDraft(setLeg(draft, i, { instrumentId: found.id }))} />
          )}
          <input type="number" step={1} value={leg.ratio} aria-label={`Leg ${i + 1} ratio`} onChange={(e) => setDraft(setLeg(draft, i, { ratio: Math.trunc(Number(e.target.value)) }))} />
          <input type="number" step={1} value={leg.multiplier} aria-label={`Leg ${i + 1} price multiplier`} onChange={(e) => setDraft(setLeg(draft, i, { multiplier: Math.trunc(Number(e.target.value)) }))} />
          <input type="checkbox" checked={leg.quote} aria-label={`Quote leg ${i + 1}`} onChange={() => setDraft(quoteLeg(draft, i))} />
          <input type="number" min={0} max={50} step={1} value={leg.payupTicks} aria-label={`Leg ${i + 1} pay-up ticks`} onChange={(e) => setDraft(setLeg(draft, i, { payupTicks: Math.trunc(Number(e.target.value)) }))} />
          <button type="button" className="tab-close" aria-label={`Remove leg ${i + 1}`} title="Remove this leg" disabled={draft.legs.length <= 2} onClick={() => setDraft(removeLeg(draft, i))}>
            ×
          </button>
        </div>
      ))}
      <div className="algo-form-row">
        <button type="button" className="btn small" disabled={draft.legs.length >= MAX_LEGS} onClick={() => setDraft(addLeg(draft))}>
          + Add leg
        </button>
        <span className="dim">{legs.length >= 2 ? `price = ${formula(legs, shortName)}` : "pick each leg's contract"}</span>
      </div>
      {controls.length > 0 && (
        <fieldset className="algo-params ase-controls">
          <legend>Controls</legend>
          {controls.map((p) => (
            <ParamInput key={p.name} param={p} value={controlText(draft, p)} onChange={(v) => setDraft({ ...draft, controls: { ...draft.controls, [p.name]: v } })} />
          ))}
        </fieldset>
      )}
      {problems.length > 0 && (
        <ul className="tone-bad ase-problems" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="algo-form-row">
        <button type="submit" className="btn" disabled={busy}>
          {spread ? "Save" : "Create spread"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

registerWidget<AutospreaderParams>({
  kind: "ase",
  title: "Autospreader",
  description: "Define spreads of 2 to 6 legs and see their live implied prices",
  component: Autospreader,
  defaults: () => ({ link: "none", mode: "list" }),
  link: "none",
  tabTitle: (p) => (p.mode === "edit" ? "Autospreader · edit" : undefined),
  size: { width: 760, height: 360 },
  menu: (p, act) => [
    { label: "Spreads", checked: (p.mode ?? "list") === "list", onSelect: () => act.setParams({ mode: "list", spreadId: undefined }) },
    { label: "New spread", onSelect: () => act.setParams({ mode: "edit", spreadId: undefined }) },
  ],
});
