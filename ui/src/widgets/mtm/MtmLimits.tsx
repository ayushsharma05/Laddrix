// MTM Limits: the trader's own loss limits, per position and for the whole account, watched
// by the server on every price (risk/mtm.py). Reaching one raises an alert (band, Messages)
// and, only if auto-close is ticked, closes through the normal order path.
//
//   Portfolio   stop ₹[20,000]  target ₹[    ]  [x] auto-close   [Set] [Remove]   day −₹3,015.00
//   Position         P&L         limit ₹   or %   auto   rule
//   NIFTY26OCTFUT    −₹1,507.50  [1,500]  [   ]   [ ]    ₹1,500.00 · alert only · reached today
//   Reached today    NIFTY26OCTFUT MIS  BLOCKED  +150 still open: refused (…)  [Retry]
//
// Each limit reached is a breach the server keeps through restarts (#263); its state and words
// come from the server. Retry (two clicks) only for a BLOCKED close: never on top of one that
// may still be happening. Saved limits that couldn't be read show in red: they aren't watched.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { MtmBreach, MtmPositionRule, MtmRules, Position } from "../../api/types";
import { useApp, useStore } from "../../app/context";
import { price, rupees, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { signedRupees } from "../books/model";
import { parsePrice } from "../ticket/model";

const tone = (n: number) => (n > 0 ? "good" : n < 0 ? "bad" : "");

export function ruleText(r: { max_loss_paise: number | null; max_loss_pct?: number | null; target_paise?: number | null; auto_close: boolean; fired_today: boolean }): string {
  const parts: string[] = [];
  if (r.max_loss_paise !== null) parts.push(`loss ${rupees(r.max_loss_paise)}`);
  if (r.max_loss_pct != null) parts.push(`loss ${r.max_loss_pct}%`);
  if (r.target_paise != null) parts.push(`target ${rupees(r.target_paise)}`);
  parts.push(r.auto_close ? "auto-close" : "alert only");
  if (r.fired_today) parts.push("reached today");
  return parts.join(" · ");
}

export function MtmLimits(_props: WidgetProps) {
  const { rest, store } = useApp();
  const accountId = useStore((s) => s.accountId);
  const book = useStore((s) => (s.accountId ? s.books[s.accountId] : undefined));
  const open = useMemo(() => Object.values(book?.positions ?? {}).filter((p) => p.qty !== 0), [book?.positions]);
  const [rules, setRules] = useState<MtmRules | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = accountId ? `/api/accounts/${encodeURIComponent(accountId)}/mtm` : null;

  useEffect(() => {
    if (!base) return;
    let live = true;
    rest
      .get<MtmRules>(base)
      .then((r) => live && setRules(r))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [rest, base, book?.positions, book?.orders]); // a close's state follows them

  const call = useCallback(
    (method: "put" | "delete", path: string, body?: unknown) => {
      setError(null);
      const done = method === "put" ? rest.put<MtmRules>(path, body) : rest.delete<MtmRules>(path);
      done
        .then((r) => {
          setRules(r);
          store.addMessage("INFO", "", "MTM limits saved");
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    },
    [rest, store],
  );

  if (!accountId || !base) return <div className="empty-note">No account selected.</div>;
  const byKey = new Map((rules?.positions ?? []).map((r) => [r.key, r]));
  const retry = (key: string) => {
    setError(null);
    rest
      .post<MtmRules>(`${base}/retry`, { key, confirm: true })
      .then((r) => {
        setRules(r);
        store.addMessage("INFO", "", `MTM ${key}: closing again`);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  return (
    <div className="book mtm">
      {error && (
        <div className="empty-note tone-bad" role="alert">
          {error}
        </div>
      )}
      {rules?.problem && (
        <div className="empty-note tone-bad" role="alert">
          {rules.problem}
        </div>
      )}
      <Breaches breaches={rules?.breaches ?? []} onRetry={retry} />
      <PortfolioForm rules={rules} dayPnl={book?.dayPnl ?? 0} onSet={(body) => call("put", `${base}/portfolio`, body)} onRemove={() => call("delete", `${base}/portfolio`)} />
      <div className="grid-head book-row mtm-row" role="row">
        <span>Position</span>
        <span className="num">P&amp;L today</span>
        <span className="num">Limit ₹</span>
        <span className="num">or %</span>
        <span>Auto</span>
        <span>Rule</span>
        <span />
      </div>
      <div className="grid-body" role="grid" aria-label="Position limits">
        {!open.length && <div className="empty-note">No open positions.</div>}
        {open.map((p) => (
          <PositionForm
            key={`${p.instrument_id}|${p.product}`}
            position={p}
            rule={byKey.get(`${p.instrument_id}|${p.product}`)}
            onSet={(body) => call("put", `${base}/positions`, body)}
            onRemove={() => call("delete", `${base}/positions?instrument_id=${encodeURIComponent(p.instrument_id)}&product=${p.product}`)}
          />
        ))}
      </div>
    </div>
  );
}

const STATE_TONE: Record<MtmBreach["state"], string> = { ALERTED: "dim", REQUESTED: "", WORKING: "", BLOCKED: "tone-bad", UNKNOWN: "tone-bad", COMPLETED: "good" };

function Breaches({ breaches, onRetry }: { breaches: MtmBreach[]; onRetry: (key: string) => void }) {
  const [armed, setArmed] = useState<string | null>(null);
  if (!breaches.length) return null;
  return (
    <div className="mtm-breaches" role="grid" aria-label="Limits reached">
      {breaches.map((b) => {
        const name = b.key === "portfolio" ? "Portfolio" : `${shortName(b.key.split("|")[0]!)} ${b.key.split("|")[1] ?? ""}`;
        return (
          <div key={b.id} role="row" className="row book-row" title={b.text}>
            <span>{name}</span>
            <span className={STATE_TONE[b.state]}>{b.state.toLowerCase()}</span>
            <span className="grow">{b.detail || b.text}</span>
            {b.state === "BLOCKED" && (
              <button
                type="button"
                className="btn small danger"
                onClick={() => {
                  if (armed === b.key) {
                    setArmed(null);
                    onRetry(b.key);
                  } else setArmed(b.key);
                }}
              >
                {armed === b.key ? "Confirm: close again" : "Retry"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PortfolioForm({
  rules,
  dayPnl,
  onSet,
  onRemove,
}: {
  rules: MtmRules | null;
  dayPnl: number;
  onSet: (body: unknown) => void;
  onRemove: () => void;
}) {
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [auto, setAuto] = useState(false);
  const rule = rules?.portfolio ?? null;
  const set = () => onSet({ max_loss_paise: stop.trim() ? parsePrice(stop) : null, target_paise: target.trim() ? parsePrice(target) : null, auto_close: auto });
  return (
    <div className="book-bar mtm-portfolio" aria-label="Portfolio stop">
      <b>Portfolio</b>
      <label>
        stop ₹ <input aria-label="Portfolio stop" value={stop} onChange={(e) => setStop(e.target.value)} placeholder="loss" />
      </label>
      <label>
        target ₹ <input aria-label="Portfolio target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="profit" />
      </label>
      <label>
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto-close
      </label>
      <button type="button" className="btn small" onClick={set}>
        Set
      </button>
      {rule && (
        <button type="button" className="btn small" onClick={onRemove}>
          Remove
        </button>
      )}
      <span className="dim">{rule ? ruleText(rule) : "no stop"}</span>
      <span className="book-spacer" />
      <span>
        day <b className={tone(dayPnl)}>{signedRupees(dayPnl)}</b>
      </span>
    </div>
  );
}

function PositionForm({ position: p, rule, onSet, onRemove }: { position: Position; rule: MtmPositionRule | undefined; onSet: (body: unknown) => void; onRemove: () => void }) {
  const [loss, setLoss] = useState("");
  const [pct, setPct] = useState("");
  const [auto, setAuto] = useState(false);
  const name = shortName(p.instrument_id);
  const set = () =>
    onSet({
      instrument_id: p.instrument_id,
      product: p.product,
      max_loss_paise: loss.trim() ? parsePrice(loss) : null,
      max_loss_pct: pct.trim() ? Number(pct) : null,
      auto_close: auto,
    });
  return (
    <div role="row" className={`row book-row mtm-row${rule?.fired_today ? " differs" : ""}`}>
      <span>
        {name} <span className="dim">{p.product}</span>
      </span>
      <span className={`num ${tone(p.pnl_paise)}`}>{signedRupees(p.pnl_paise)}</span>
      <input aria-label={`${name} loss limit`} className="num" value={loss} onChange={(e) => setLoss(e.target.value)} placeholder={rule?.max_loss_paise ? price(rule.max_loss_paise) : ""} />
      <input aria-label={`${name} loss percent`} className="num" value={pct} onChange={(e) => setPct(e.target.value)} placeholder={rule?.max_loss_pct ? String(rule.max_loss_pct) : ""} />
      <input aria-label={`${name} auto-close`} type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
      <span className={rule?.fired_today ? "tone-bad" : "dim"}>{rule ? ruleText(rule) : "–"}</span>
      <span>
        <button type="button" className="btn small" onClick={set}>
          Set
        </button>
        {rule && (
          <button type="button" className="btn small" onClick={onRemove}>
            ×
          </button>
        )}
      </span>
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "mtm",
  title: "MTM Limits",
  description: "Your own loss limits per position and for the account; alert, or close automatically",
  component: MtmLimits,
  defaults: () => ({ link: "none" }),
  link: "none",
});
