// Strategy builder: pick a template (or Alt-click legs in the option chain), edit the legs,
// and see what the position does before trading it (K8.4).
//
//   NIFTY ▾  27 Oct ▾   Iron condor ▾  width [2]  lots [1]  [Build]
//   BUY  1 × 24,800 PE  @ 20.00  IV 12.3   ⇄ − +  ×
//   SELL 1 × 24,900 PE  @ 40.00  …
//   ┌ payoff chart: at expiry (solid), today (dashed), breakevens, the forward ┐
//   Credit ₹3,000 · Max profit +₹3,000 · Max loss −₹4,500 · Breakevens 24,860 / 25,140
//   Net Δ −0.54 · Γ … · Θ +₹446/day · ν …
//
// Every number is the server's (options/strategy.py); the chart is its curves, scaled.

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/rest";
import type { StrategyAnalysis, StrategyLegIn, StrategyTemplate } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { price, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { expiryLabel, strikeText } from "../chain/model";
import { addLeg, chart, flip, money, premiumText, removeLeg, setLots } from "./model";

export interface StrategyParams extends BaseParams {
  underlying?: string;
  expiry?: string;
  template?: string;
  width: number;
  lots: number;
  legs: StrategyLegIn[];
}

const BOX = { width: 960, height: 260, pad: 28 };
const REFRESH_MS = 3000; // today's curve moves with prices: re-ask the server while open

const errorText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export function StrategyBuilder({ params, setParams }: WidgetProps<StrategyParams>) {
  const { rest, store } = useApp();
  const inbox = useStore((s) => s.strategyInbox);
  const [templates, setTemplates] = useState<StrategyTemplate[]>([]);
  const [underlyings, setUnderlyings] = useState<string[]>([]);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [analysis, setAnalysis] = useState<StrategyAnalysis | null>(null);
  const [problem, setProblem] = useState("");
  const legs = useMemo(() => params.legs ?? [], [params.legs]);
  const underlying = params.underlying ?? underlyings[0];
  const expiry = params.expiry && expiries.includes(params.expiry) ? params.expiry : expiries[0];
  const template = templates.find((t) => t.code === params.template) ?? templates[0];

  useEffect(() => store.registerStrategyBuilder(), [store]);

  // legs Alt-clicked in the option chain
  useEffect(() => {
    if (!inbox.length) return;
    const taken = store.takeStrategyInbox();
    setParams({ legs: taken.reduce(addLeg, legs) });
  }, [inbox, store, legs, setParams]);

  useEffect(() => {
    let live = true;
    rest.get<StrategyTemplate[]>("/api/options/strategies").then((t) => live && setTemplates(t)).catch(() => {});
    rest.get<string[]>("/api/options").then((u) => live && setUnderlyings(u)).catch(() => {});
    return () => {
      live = false;
    };
  }, [rest]);

  useEffect(() => {
    if (!underlying) return;
    let live = true;
    rest
      .get<string[]>(`/api/options/${encodeURIComponent(underlying)}/expiries`)
      .then((e) => live && setExpiries(e))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [rest, underlying]);

  // the server analyses the legs now, and again every few seconds (live prices)
  const key = JSON.stringify(legs);
  useEffect(() => {
    setProblem("");
    if (!legs.length) {
      setAnalysis(null);
      return;
    }
    let live = true;
    const ask = () =>
      rest
        .post<StrategyAnalysis>("/api/options/strategy/analyse", { legs })
        .then((a) => live && setAnalysis(a))
        .catch((e: unknown) => {
          if (!live) return;
          setAnalysis(null);
          setProblem(errorText(e));
        });
    void ask();
    const timer = setInterval(ask, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
    // `key` changes exactly when the legs do (a new array with the same legs asks nothing)
  }, [rest, key]);

  const build = () => {
    if (!template || !underlying || !expiry) return;
    rest
      .post<StrategyAnalysis>("/api/options/strategy/build", { code: template.code, underlying, expiry, width: params.width ?? 2, lots: params.lots ?? 1 })
      .then((a) => {
        setAnalysis(a);
        setProblem("");
        setParams({ legs: a.legs.map((l) => ({ instrument_id: l.instrument_id, side: l.side, lots: l.lots })), underlying, expiry, template: template.code });
      })
      .catch((e: unknown) => setProblem(errorText(e)));
  };

  const drawn = useMemo(() => (analysis ? chart(analysis, BOX) : null), [analysis]);
  const legInfo = (id: string) => analysis?.legs.find((l) => l.instrument_id === id);

  return (
    <div className="strategy">
      <div className="book-bar strategy-bar">
        <select aria-label="Underlying" value={underlying ?? ""} onChange={(e) => setParams({ underlying: e.target.value, expiry: undefined })}>
          {underlyings.map((u) => (
            <option key={u}>{u}</option>
          ))}
        </select>
        <select aria-label="Expiry" value={expiry ?? ""} onChange={(e) => setParams({ expiry: e.target.value })}>
          {expiries.map((e) => (
            <option key={e} value={e}>
              {expiryLabel(e)}
            </option>
          ))}
        </select>
        <select aria-label="Strategy" value={template?.code ?? ""} onChange={(e) => setParams({ template: e.target.value })} title={template?.view}>
          {templates.map((t) => (
            <option key={t.code} value={t.code}>
              {t.name}
            </option>
          ))}
        </select>
        <label className="strategy-num">
          <span className="dim">Width</span>
          <input aria-label="Width (strikes)" inputMode="numeric" value={params.width ?? 2} onChange={(e) => setParams({ width: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })} size={2} />
        </label>
        <label className="strategy-num">
          <span className="dim">Lots</span>
          <input aria-label="Lots" inputMode="numeric" value={params.lots ?? 1} onChange={(e) => setParams({ lots: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })} size={2} />
        </label>
        <button type="button" className="btn small" onClick={build} disabled={!template || !expiry}>
          Build
        </button>
        <span className="book-spacer" />
        <button type="button" className="btn small" disabled={!legs.length} onClick={() => setParams({ legs: [] })}>
          Clear
        </button>
      </div>
      {template && <div className="strategy-view dim">{template.name}: {template.view}</div>}
      <div className="strategy-body">
        <div className="strategy-legs" role="table" aria-label="Legs">
          {!legs.length && <div className="empty-note">Build a template, or Alt-click calls and puts in the option chain (Alt+Shift for a sell).</div>}
          {legs.map((leg, i) => {
            const info = legInfo(leg.instrument_id);
            return (
              <div key={`${leg.instrument_id}-${leg.side}`} role="row" className="strategy-leg">
                <button type="button" className={`seg-side ${leg.side === "BUY" ? "buy-text" : "sell-text"}`} title="Buy / sell" aria-label={`Flip ${leg.side} ${shortName(leg.instrument_id)}`} onClick={() => setParams({ legs: flip(legs, i) })}>
                  {leg.side}
                </button>
                <span className="strategy-lots">
                  <button type="button" aria-label={`Fewer lots ${shortName(leg.instrument_id)}`} onClick={() => setParams({ legs: setLots(legs, i, leg.lots - 1) })}>
                    −
                  </button>
                  {leg.lots}
                  <button type="button" aria-label={`More lots ${shortName(leg.instrument_id)}`} onClick={() => setParams({ legs: setLots(legs, i, leg.lots + 1) })}>
                    +
                  </button>
                </span>
                <span>{info ? `${strikeText(info.strike_paise)} ${info.kind} · ${expiryLabel(info.expiry)}` : shortName(leg.instrument_id)}</span>
                <span className="num">{info ? `@ ${price(info.price_paise)}` : ""}</span>
                <span className="num dim">{info?.iv ? `IV ${(info.iv * 100).toFixed(1)}` : ""}</span>
                <button type="button" className="row-action" aria-label={`Remove ${shortName(leg.instrument_id)}`} onClick={() => setParams({ legs: removeLeg(legs, i) })}>
                  ×
                </button>
              </div>
            );
          })}
          {problem && (
            <div className="algo-problems" role="alert">
              {problem}
            </div>
          )}
        </div>
        {analysis && drawn && (
          <div className="strategy-result">
            <svg viewBox={`0 0 ${BOX.width} ${BOX.height}`} className="payoff" role="img" aria-label="Payoff chart">
              <path d={drawn.profitArea} className="payoff-profit" />
              <line x1={BOX.pad} x2={BOX.width - BOX.pad} y1={drawn.zeroY} y2={drawn.zeroY} className="payoff-zero" />
              <line x1={drawn.forwardX} x2={drawn.forwardX} y1={BOX.pad} y2={BOX.height - BOX.pad} className="payoff-forward" />
              {drawn.breakevenXs.map((x) => (
                <line key={x} x1={x} x2={x} y1={drawn.zeroY - 6} y2={drawn.zeroY + 6} className="payoff-be" />
              ))}
              <path d={drawn.todayPath} className="payoff-today" />
              <path d={drawn.expiryPath} className="payoff-expiry" />
              <text x={BOX.pad} y={BOX.height - 8} className="payoff-axis">
                {Math.round(drawn.xMin).toLocaleString("en-IN")}
              </text>
              <text x={BOX.width - BOX.pad} y={BOX.height - 8} className="payoff-axis" textAnchor="end">
                {Math.round(drawn.xMax).toLocaleString("en-IN")}
              </text>
              <text x={drawn.forwardX + 4} y={BOX.pad + 10} className="payoff-axis">
                now {Math.round(analysis.forward_paise / 100).toLocaleString("en-IN")}
              </text>
              <text x={4} y={BOX.pad} className="payoff-axis">
                {money(drawn.yMax)}
              </text>
              <text x={4} y={BOX.height - BOX.pad} className="payoff-axis">
                {money(drawn.yMin)}
              </text>
            </svg>
            <div className="strategy-stats" aria-label="Strategy figures">
              <span>
                <b>{premiumText(analysis.net_premium)}</b>
              </span>
              <span>
                Max profit <b className="tone-good">{money(analysis.max_profit)}</b>
              </span>
              <span>
                Max loss <b className="tone-bad">{money(analysis.max_loss)}</b>
              </span>
              <span>
                Breakevens <b>{analysis.breakevens.length ? analysis.breakevens.map((b) => b.toLocaleString("en-IN", { maximumFractionDigits: 2 })).join(" / ") : "none"}</b>
              </span>
              <span className="dim">at {expiryLabel(analysis.expiry)} expiry · solid: at expiry · dashed: today</span>
            </div>
            <div className="strategy-stats" aria-label="Net Greeks">
              <span>
                Net Δ <b>{analysis.net_greeks.delta.toFixed(2)}</b>
              </span>
              <span>
                Γ <b>{analysis.net_greeks.gamma.toFixed(4)}</b>
              </span>
              <span>
                Θ <b>{money(analysis.net_greeks.theta)}/day</b>
              </span>
              <span>
                ν <b>{money(analysis.net_greeks.vega)}/IV pt</b>
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

registerWidget<StrategyParams>({
  kind: "strategy",
  title: "Strategy Builder",
  description: "Multi-leg option strategies: templates, payoff, breakevens, Greeks",
  component: StrategyBuilder,
  defaults: () => ({ link: "none", width: 2, lots: 1, legs: [] }),
  link: "none",
});
