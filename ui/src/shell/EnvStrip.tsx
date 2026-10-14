// The strip under the toolbar: which world orders go to (paper, live, none), and for a
// broker with a daily login that hasn't happened today, the button that starts it.

import { useState } from "react";
import { useApp, useStore } from "../app/context";
import { brokerName } from "../state/gate";

export function EnvStrip() {
  const me = useStore((s) => s.me);
  const accountId = useStore((s) => s.accountId);
  const account = me?.accounts.find((a) => a.id === accountId);
  if (!account) return <div className="env-strip none">NO ACCOUNT · you can watch prices but not trade</div>;
  if (account.broker === "paper") {
    return (
      <div className="env-strip paper">
        PAPER · {account.id} · orders fill against live prices; nothing goes to the exchange
      </div>
    );
  }
  if (account.broker_session === "login_needed") {
    return (
      <div className="env-strip login-needed" role="status">
        LIVE · {account.broker.toUpperCase()} · {account.broker_client_id} · not logged in to {brokerName(account.broker)} today: orders are refused
        {account.can_trade && <BrokerLoginButton accountId={account.id} broker={account.broker} />}
      </div>
    );
  }
  return (
    <div className="env-strip live">
      LIVE · {account.broker.toUpperCase()} · {account.broker_client_id} · orders go to the exchange
    </div>
  );
}

/** The broker's daily login happens on the broker's own page; it sends the user back here. */
function BrokerLoginButton({ accountId, broker }: { accountId: string; broker: string }) {
  const { rest, store } = useApp();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn small strip-action"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        rest
          .get<{ url: string }>(`/api/accounts/${encodeURIComponent(accountId)}/broker-login`)
          .then(({ url }) => window.location.assign(url))
          .catch((e: unknown) => {
            store.addMessage("ERROR", "", `${brokerName(broker)} login: ${e instanceof Error ? e.message : String(e)}`);
            setBusy(false);
          });
      }}
    >
      Log in to {brokerName(broker)}
    </button>
  );
}
