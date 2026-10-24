// Structure positions (#428): the spreads an account holds, from the fills allocated to them.
//
//   [New from spread ▾]                                                   3 structures
//   ▾ NIFTY Oct/Nov   +1 lot   avg −130.00   mark −128.60   P&L +₹105   realised +₹750
//       NIFTY26OCTFUT  +75 @ 25,010.00   P&L +₹75     residual 0
//       NIFTY26NOVFUT  −75 @ 25,140.00   P&L +₹30     residual 0
//       MT12  BUY 75 NIFTY26OCTFUT  [Remove] [Move to ▾]
//   Unallocated     NIFTY26OCTFUT +75
//   Fills not in a structure   MT20 BUY 75 NIFTY26OCTFUT  [75] [Add to ▾]
//
// The server works out every figure and checks every change (it says why one can't be done);
// an Autospreader's fills are put in its spread's structure by the server, at once.

import { useCallback, useEffect, useState } from "react";
import type { Spread, Structures as StructuresBody, StructureView } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { price, qty, rupees, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const tone = (n: number) => (n > 0 ? "tone-good" : n < 0 ? "tone-bad" : "");
const signed = (n: number) => (n > 0 ? `+${qty(n)}` : qty(n));

export function Structures(_props: WidgetProps) {
  const { rest } = useApp();
  const accountId = useStore((s) => s.accountId);
  // refreshed whenever the account's orders or positions change (a fill, an exit)
  const orders = useStore((s) => (s.accountId ? s.books[s.accountId]?.orders : undefined));
  const positions = useStore((s) => (s.accountId ? s.books[s.accountId]?.positions : undefined));
  const [data, setData] = useState<StructuresBody | null>(null);
  const [spreads, setSpreads] = useState<Spread[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}/structures` : null;

  const load = useCallback(() => {
    if (!base) return;
    rest
      .get<StructuresBody>(base)
      .then(setData)
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest, base]);
  useEffect(load, [load, orders, positions]);
  useEffect(() => {
    rest
      .get<Spread[]>("/api/spreads")
      .then(setSpreads)
      .catch(() => undefined);
  }, [rest]);

  const act = (path: string, body: unknown, method: "post" | "delete" = "post") => {
    setProblem(null);
    const call = method === "post" ? rest.post(path, body) : rest.delete(path);
    call.then(load).catch((e: unknown) => setProblem(errorText(e)));
  };

  if (!accountId) return <div className="empty-note">Pick an account.</div>;
  if (!data) return <div className={`empty-note${problem ? " tone-bad" : ""}`}>{problem ?? "loading…"}</div>;
  const others = (s: StructureView) => data.structures.filter((x) => x.structure_id !== s.structure_id);
  return (
    <div className="book structures">
      <div className="book-bar">
        <select
          aria-label="New structure from a spread"
          value=""
          onChange={(e) => e.target.value && base && act(base, { spread_id: e.target.value })}
        >
          <option value="">New from spread…</option>
          {spreads.map((s) => (
            <option key={s.spread_id} value={s.spread_id}>
              {s.name}
            </option>
          ))}
        </select>
        <span className="book-spacer" />
        <span className="dim">{data.structures.length} structures</span>
      </div>
      {problem && (
        <div className="empty-note tone-bad" role="alert">
          {problem}
        </div>
      )}
      <div className="book-scroll">
        {!data.structures.length && <div className="empty-note">No structures yet: an Autospreader's fills make one, or make one from a spread.</div>}
        {data.structures.map((s) => (
          <div key={s.structure_id} className="structure" role="group" aria-label={s.name}>
            <div
              className="row book-row structure-row"
              role="button"
              tabIndex={0}
              aria-expanded={!!open[s.structure_id]}
              aria-label={`Open ${s.name}`}
              onClick={() => setOpen((o) => ({ ...o, [s.structure_id]: !o[s.structure_id] }))}
              onKeyDown={(e) => e.key === "Enter" && setOpen((o) => ({ ...o, [s.structure_id]: !o[s.structure_id] }))}
            >
              <span className="strong">
                {open[s.structure_id] ? "▾" : "▸"} {s.name}
              </span>
              <span className={s.lots > 0 ? "buy-text" : s.lots < 0 ? "sell-text" : "dim"} aria-label={`${s.name} lots`}>
                {signed(s.lots)} {Math.abs(s.lots) === 1 ? "lot" : "lots"}
              </span>
              <span>avg {s.avg_spread_paise === null ? "–" : price(s.avg_spread_paise)}</span>
              <span>mark {s.mark_paise === null ? "–" : price(s.mark_paise)}</span>
              <span className={tone(s.pnl_paise)} aria-label={`${s.name} P&L`}>
                P&L {rupees(s.pnl_paise, { sign: true })}
              </span>
              <span className="dim">realised {rupees(s.realised_paise, { sign: true })}</span>
            </div>
            {s.note && <div className="empty-note">{s.note}</div>}
            {open[s.structure_id] && (
              <div className="algo-detail">
                {s.legs.map((g) => (
                  <div key={g.instrument_id} className="algo-child">
                    <span>{shortName(g.instrument_id)}</span>
                    <span className={g.qty > 0 ? "buy-text" : g.qty < 0 ? "sell-text" : "dim"}>{signed(g.qty)}</span>
                    <span>@ {g.qty ? price(g.avg_price_paise) : "–"}</span>
                    <span className={tone(g.realised_paise + g.unrealised_paise)}>P&L {rupees(g.realised_paise + g.unrealised_paise, { sign: true })}</span>
                    <span className={g.residual_qty ? "tone-warn" : "dim"} aria-label={`${shortName(g.instrument_id)} residual`}>
                      residual {signed(g.residual_qty)}
                    </span>
                  </div>
                ))}
                {s.allocations.map((a) => (
                  <div key={a.client_order_id} className="algo-child">
                    <span className="dim">{a.client_order_id}</span>
                    <span className={a.side === "BUY" ? "buy-text" : "sell-text"}>{a.side}</span>
                    <span>
                      {qty(a.qty)} {a.instrument_id ? shortName(a.instrument_id) : ""}
                    </span>
                    <button type="button" className="btn small" onClick={() => base && act(`${base}/${s.structure_id}/allocations/remove`, { client_order_id: a.client_order_id, qty: a.qty })}>
                      Remove
                    </button>
                    {others(s).length > 0 && (
                      <select
                        aria-label={`Move ${a.client_order_id} to`}
                        value=""
                        onChange={(e) => e.target.value && base && act(`${base}/${s.structure_id}/move`, { to: e.target.value, client_order_id: a.client_order_id, qty: a.qty })}
                      >
                        <option value="">Move to…</option>
                        {others(s).map((x) => (
                          <option key={x.structure_id} value={x.structure_id}>
                            {x.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                ))}
                {!s.allocations.length && (
                  <button type="button" className="btn small" onClick={() => base && act(`${base}/${s.structure_id}`, null, "delete")}>
                    Delete (empty)
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        {data.unallocated.length > 0 && (
          <div className="structure-unallocated" role="group" aria-label="Unallocated">
            <span className="dim">Unallocated</span>
            {data.unallocated.map((u) => (
              <span key={u.instrument_id} className={u.qty > 0 ? "buy-text" : "sell-text"}>
                {shortName(u.instrument_id)} {signed(u.qty)}
              </span>
            ))}
          </div>
        )}
        {data.free_fills.length > 0 && data.structures.length > 0 && (
          <div role="group" aria-label="Fills not in a structure">
            <div className="dim">Fills not in a structure</div>
            {data.free_fills.map((f) => (
              <FreeFill key={f.client_order_id} fill={f} structures={data.structures} onAdd={(id, n) => base && act(`${base}/${id}/allocations`, { client_order_id: f.client_order_id, qty: n })} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function FreeFill({ fill, structures, onAdd }: { fill: StructuresBody["free_fills"][number]; structures: StructureView[]; onAdd: (structureId: string, qty: number) => void }) {
  const [n, setN] = useState(String(fill.unallocated_qty));
  return (
    <div className="algo-child">
      <span className="dim">{fill.client_order_id}</span>
      <span className={fill.side === "BUY" ? "buy-text" : "sell-text"}>{fill.side}</span>
      <span>
        {qty(fill.unallocated_qty)} {shortName(fill.instrument_id)} @ {price(fill.avg_price_paise)}
      </span>
      <input aria-label={`Quantity of ${fill.client_order_id}`} value={n} size={6} onChange={(e) => setN(e.target.value)} />
      <select aria-label={`Add ${fill.client_order_id} to`} value="" onChange={(e) => e.target.value && onAdd(e.target.value, Number(n))}>
        <option value="">Add to…</option>
        {structures.map((s) => (
          <option key={s.structure_id} value={s.structure_id}>
            {s.name}
          </option>
        ))}
      </select>
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "structures",
  title: "Structures",
  description: "Spreads held: their lots, legs and P&L from the fills allocated to them, and what is left over",
  component: Structures,
  defaults: () => ({ link: "none" }),
  link: "none",
  size: { width: 760, height: 360 },
});
