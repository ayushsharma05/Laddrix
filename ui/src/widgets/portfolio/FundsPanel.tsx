// Funds: what the account can still use, what is blocked, and why, per segment, as the broker
// reports it (paper: worked out by MockTrade, marked "estimate").
//
//   EQUITY                                   as of 10:31:05  ↻
//   Available  ₹9,72,505.05     Used ₹27,494.95     Opening ₹10,00,000.00
//   Opening balance   10,00,000.00     SPAN + exposure   0.00
//   Realised P&L today    +1,250.00    Options premium  27,494.95

import type { Funds } from "../../api/types";
import { rupees } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { freshness } from "./model";
import { usePortfolio } from "./usePortfolio";

export function FundsPanel(_props: WidgetProps) {
  const { accountId, snapshot, missing, refreshing, refresh } = usePortfolio("funds");
  const fresh = freshness(snapshot, missing);
  const items = (snapshot?.items ?? []) as Funds[];
  return (
    <div className="book">
      <div className="book-bar">
        <span className={`tone-${fresh.tone}`} title={fresh.title}>
          {fresh.text}
        </span>
        <span className="book-spacer" />
        <button type="button" className="btn small" disabled={!accountId || !!missing || refreshing} onClick={refresh} title="Ask the broker now">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <div className="book-scroll funds">
        {!accountId && <div className="empty-note">No account selected.</div>}
        {accountId && snapshot && !items.length && !missing && <div className="empty-note">The broker reported no segments.</div>}
        {items.map((f) => (
          <section key={f.segment} className="funds-segment" aria-label={`${f.segment} funds`}>
            <h3>
              {f.segment.toUpperCase()}
              {f.estimate && (
                <span className="algo-family" title="Paper money: worked out by MockTrade with plain margin rates, not a broker's figures">
                  estimate
                </span>
              )}
            </h3>
            <div className="funds-heads">
              <span>
                Available <b className={f.available_paise < 0 ? "bad" : "good"}>{rupees(f.available_paise)}</b>
              </span>
              <span>
                Used <b>{rupees(f.used_paise)}</b>
              </span>
              <span>
                Opening <b>{rupees(f.opening_paise)}</b>
              </span>
              {f.collateral_paise !== 0 && (
                <span>
                  Collateral <b>{rupees(f.collateral_paise)}</b>
                </span>
              )}
            </div>
            <div className="funds-lines">
              <Lines title="Available" lines={f.available_lines} />
              <Lines title="Used" lines={f.used_lines} />
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function Lines({ title, lines }: { title: string; lines: Funds["available_lines"] }) {
  return (
    <dl aria-label={title}>
      {lines.map((line) => (
        <div key={line.label}>
          <dt>{line.label}</dt>
          <dd className="num">{rupees(line.paise)}</dd>
        </div>
      ))}
    </dl>
  );
}

registerWidget<BaseParams>({
  kind: "funds",
  title: "Funds",
  description: "Available cash and margin, what is used and why, per segment",
  component: FundsPanel,
  defaults: () => ({ link: "none" }),
  link: "none",
});
