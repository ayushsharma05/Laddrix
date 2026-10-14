// The bands under the toolbar: only when something needs attention, most serious first
// (docs/design/ui.md §3).

import { useApp, useNow, useStore } from "../app/context";
import { clock, shortName } from "../lib/format";

export const PRICES_OLD_MS = 5000;

export function Bands() {
  const { stream } = useApp();
  const now = useNow(1000);
  const status = useStore((s) => s.stream);
  const firm = useStore((s) => s.firmKill);
  const accountId = useStore((s) => s.accountId);
  const kill = useStore((s) => (s.accountId ? s.books[s.accountId]?.kill : undefined));
  const alerts = useStore((s) => (s.accountId ? s.books[s.accountId]?.alerts : undefined));
  const ops = useStore((s) => s.ops);

  const bands = [];
  if (firm.engaged) {
    bands.push(
      <div key="firm" role="alert" className="band firm">
        <b>ALL TRADING STOPPED (firm)</b>
        <span>
          {firm.by ? `by ${firm.by} · ` : ""}
          {firm.reason ? `"${firm.reason}" · ` : ""}only an admin can resume
        </span>
      </div>,
    );
  }
  if (kill?.engaged) {
    bands.push(
      <div key="stop" role="alert" className="band stopped">
        <b>TRADING STOPPED · {accountId}</b>
        <span>
          {kill.reason}
          {kill.by ? ` · by ${kill.by}` : ""} · working orders cancelled · positions kept · new orders blocked
        </span>
      </div>,
    );
  }
  // operational alerts from the server: errors first (Kite login, prices stopped, disk)
  const opsAlerts = Object.values(ops).sort((a, b) => (a.level === b.level ? a.key.localeCompare(b.key) : a.level === "ERROR" ? -1 : 1));
  for (const a of opsAlerts) {
    bands.push(
      <div key={`ops-${a.key}`} role={a.level === "ERROR" ? "alert" : "status"} className={`band ${a.level === "ERROR" ? "stopped" : "warn"}`}>
        <b>{a.level === "ERROR" ? "ACTION NEEDED" : "HEADS UP"}</b>
        <span>{a.text}</span>
      </div>,
    );
  }
  if (status === "reconnecting" || status === "connecting") {
    bands.push(
      <div key="disc" role="status" className="band warn">
        <b>{status === "connecting" ? "CONNECTING TO MOCKTRADE" : "DISCONNECTED FROM MOCKTRADE"}</b>
        <span>reconnecting… order clicks paused so nothing is sent blind · orders at the broker are unaffected</span>
      </div>,
    );
  } else if (status === "open" && stream.watched().length > 0 && stream.lastTickAt !== null) {
    const age = now - stream.lastTickAt;
    if (age > PRICES_OLD_MS) {
      bands.push(
        <div key="old" role="status" className="band warn">
          <b>PRICES {Math.round(age / 1000)} s OLD</b>
          <span>last price at {clock(new Date(stream.lastTickAt).toISOString(), false)} · the market may be closed or the feed is down</span>
        </div>,
      );
    }
  }
  for (const alert of Object.values(alerts ?? {})) {
    bands.push(
      <div key={`recon-${alert.instrumentId}`} role="alert" className="band warn">
        <b>POSITION DIFFERS · {shortName(alert.instrumentId)}</b>
        <span>
          MockTrade {signed(alert.ours)} · broker {signed(alert.broker)} · check the broker before trading it
        </span>
      </div>,
    );
  }
  return bands.length ? <div className="bands">{bands}</div> : null;
}

function signed(n: number): string {
  return n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "0";
}
