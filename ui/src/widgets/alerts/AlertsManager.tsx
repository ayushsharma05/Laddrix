// Alerts: your price, % move, volume, OI and spread alerts (K12.4; the engine is K12.3,
// docs/design/charts.md). The New form is drawn from the server's alert spec; the list follows
// the server live (stream "market_alert_state"); a fired alert also lands in Messages.
//
//   [Alerts] New  Webhook
//   NIFTY26OCTFUT  Price crosses above 25,100.00   watching          0×            On  Delete…
//   RELIANCE       Moves from the previous close 2% · up   fired 1×  11:42  sent ✓
//
// Webhook: your https address; the secret to check our signature is shown once, when it is set.

import { useEffect, useState } from "react";
import type { AlertSpec, MarketAlert, WebhookSettings } from "../../api/types";
import { useApp } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { ParamInput } from "../algos/AlgoManager";
import { byFamily, familyLabel, initialValues, type FormValues } from "../algos/model";
import { buildAlertBody, describe, stateTone, upsert } from "./model";

export interface AlertsParams extends BaseParams {
  mode?: "list" | "new" | "webhook";
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function AlertsManager({ params, setParams, publish }: WidgetProps<AlertsParams>) {
  const { rest, stream } = useApp();
  const mode = params.mode ?? "list";
  const [spec, setSpec] = useState<AlertSpec | null>(null);
  const [alerts, setAlerts] = useState<MarketAlert[]>([]);
  const [hook, setHook] = useState<WebhookSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([rest.get<AlertSpec>("/api/alerts/spec"), rest.get<MarketAlert[]>("/api/alerts"), rest.get<WebhookSettings>("/api/alerts/webhook")])
      .then(([s, list, h]) => {
        if (!live) return;
        setSpec(s);
        setAlerts(list);
        setHook(h);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest]);

  // live: every change the server makes to one of our alerts
  useEffect(() => {
    const offs = [stream.on("market_alert_state", (m) => setAlerts((list) => upsert(list, m.alert))), stream.on("market_alert", (m) => setAlerts((list) => upsert(list, m.alert)))];
    return () => offs.forEach((off) => off());
  }, [stream]);

  if (error && !spec) return <div className="empty-note tone-bad">{error}</div>;
  if (!spec) return <div className="empty-note">loading…</div>;
  return (
    <div className="book alerts">
      <div className="book-bar">
        {(["list", "new", "webhook"] as const).map((m) => (
          <button key={m} type="button" className={`book-tab${mode === m ? " on" : ""}`} aria-pressed={mode === m} onClick={() => setParams({ mode: m })}>
            {m === "list" ? `Alerts (${alerts.filter((a) => a.active).length})` : m === "new" ? "New" : "Webhook"}
          </button>
        ))}
        <span className="book-spacer" />
        <span className="dim">
          {alerts.length} of {spec.max_alerts}
        </span>
      </div>
      <div className="book-scroll alerts-body">
        {mode === "list" && <AlertList spec={spec} alerts={alerts} onChange={(a) => setAlerts((l) => upsert(l, a))} onGone={(id) => setAlerts((l) => l.filter((a) => a.alert_id !== id))} publish={publish} />}
        {mode === "new" && (
          <NewAlert
            spec={spec}
            defaultInstrument={params.instrumentId ?? null}
            webhookSet={hook?.set ?? false}
            onMade={(a) => {
              setAlerts((l) => upsert(l, a));
              setParams({ mode: "list" });
            }}
          />
        )}
        {mode === "webhook" && <Webhook hook={hook} onChange={setHook} />}
      </div>
    </div>
  );
}

function AlertList({ spec, alerts, onChange, onGone, publish }: { spec: AlertSpec; alerts: MarketAlert[]; onChange: (a: MarketAlert) => void; onGone: (id: string) => void; publish: (id: string) => void }) {
  const { rest } = useApp();
  const [armed, setArmed] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const toggle = (a: MarketAlert) =>
    rest
      .patch<MarketAlert>(`/api/alerts/${a.alert_id}`, { enabled: !(a.enabled && !a.done) })
      .then(onChange)
      .catch((e: unknown) => setProblem(errorText(e)));
  const remove = (id: string) =>
    rest
      .delete(`/api/alerts/${id}`)
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
      <div className="grid-head book-row alert-row" role="row">
        <span>Contract</span>
        <span>Alert</span>
        <span>State</span>
        <span className="num">Fired</span>
        <span>Last</span>
        <span>Webhook</span>
        <span />
      </div>
      <div className="grid-body" role="grid" aria-label="Alerts">
        {!alerts.length && <div className="empty-note">No alerts yet: make one from New.</div>}
        {alerts.map((a) => (
          <div key={a.alert_id} role="row" className={`row book-row alert-row${a.active ? "" : " dim"}`} title={a.last_text || a.note} onClick={() => publish(a.instrument_id)}>
            <span>{shortName(a.instrument_id)}</span>
            <span>
              {describe(spec, a)}
              {a.repeat === "every" && <span className="dim"> · every time</span>}
              {a.note && <span className="dim"> · {a.note}</span>}
            </span>
            <span className={`tone-${stateTone(a)}`}>{a.enabled ? a.status : "off"}</span>
            <span className="num">{a.fired_count}×</span>
            <span className="dim">{a.last_fired_at ? clock(a.last_fired_at, false) : "–"}</span>
            <span className={a.webhook_error ? "tone-bad" : "dim"} title={a.webhook_error}>
              {!a.webhook ? "" : a.webhook_error ? "failed" : a.webhook_ok_at ? "sent ✓" : "on"}
            </span>
            <span className="alert-actions" onClick={(e) => e.stopPropagation()}>
              <button type="button" className="btn small" onClick={() => void toggle(a)}>
                {a.enabled && !a.done ? "Off" : "On"}
              </button>
              {armed === a.alert_id ? (
                <button type="button" className="btn small danger" onClick={() => void remove(a.alert_id)}>
                  Confirm delete
                </button>
              ) : (
                <button type="button" className="btn small" onClick={() => setArmed(a.alert_id)}>
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

function NewAlert({ spec, defaultInstrument, webhookSet, onMade }: { spec: AlertSpec; defaultInstrument: string | null; webhookSet: boolean; onMade: (a: MarketAlert) => void }) {
  const { rest } = useApp();
  const [code, setCode] = useState(spec.kinds[0]?.code ?? "");
  const kind = spec.kinds.find((k) => k.code === code);
  const [values, setValues] = useState<FormValues>(() => (kind ? initialValues(kind) : {}));
  const [instrumentId, setInstrumentId] = useState<string | null>(defaultInstrument);
  const [repeat, setRepeat] = useState(spec.repeat[0] ?? "once");
  const [cooldown, setCooldown] = useState(String(spec.cooldown_s.default));
  const [note, setNote] = useState("");
  const [webhook, setWebhook] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const pick = (next: string) => {
    const chosen = spec.kinds.find((k) => k.code === next);
    if (!chosen) return;
    setCode(next);
    setValues(initialValues(chosen));
    setProblems([]);
  };

  const submit = () => {
    const { body, problems: found } = buildAlertBody(spec, { kind, instrumentId, values, repeat, cooldown, note, webhook });
    setProblems(found);
    if (!body) return;
    setBusy(true);
    rest
      .post<MarketAlert>("/api/alerts", body)
      .then(onMade)
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };

  return (
    <form
      className="algo-form"
      aria-label="New alert"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Watch for</span>
          <select aria-label="Alert kind" value={code} onChange={(e) => pick(e.target.value)}>
            {byFamily(spec.kinds).map(([family, list]) => (
              <optgroup key={family} label={familyLabel(family)}>
                {list.map((k) => (
                  <option key={k.code} value={k.code}>
                    {k.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <span className="algo-help dim">{kind?.help}</span>
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
            <SearchBox placeholder="Search product…" ariaLabel="Alert contract" onPick={(i) => setInstrumentId(i.id)} />
          )}
        </div>
        {kind?.params.map((p) => (
          <ParamInput key={`${code}.${p.name}`} param={p} value={values[p.name]} onChange={(v) => setValues((old) => ({ ...old, [p.name]: v }))} />
        ))}
      </div>
      <div className="algo-form-row">
        <div className="algo-field">
          <span>Repeat</span>
          <select aria-label="Repeat" value={repeat} onChange={(e) => setRepeat(e.target.value)}>
            {spec.repeat.map((r) => (
              <option key={r} value={r}>
                {r === "once" ? "once" : "every time it crosses"}
              </option>
            ))}
          </select>
        </div>
        {repeat !== "once" && (
          <div className="algo-field" title="The least time between two firings">
            <span>Cool-down</span>
            <div className="algo-control">
              <input aria-label="Cool-down" inputMode="numeric" size={6} value={cooldown} onChange={(e) => setCooldown(e.target.value)} />
              <span className="dim">s</span>
            </div>
          </div>
        )}
        <div className="algo-field wide">
          <span>Note (optional)</span>
          <input aria-label="Note" maxLength={spec.note_max} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <label className="algo-field" title={webhookSet ? "Also send it to your webhook" : "Set a webhook first (the Webhook tab)"}>
          <input type="checkbox" aria-label="Send to my webhook" checked={webhook} disabled={!webhookSet} onChange={(e) => setWebhook(e.target.checked)} />
          <span>Webhook</span>
        </label>
      </div>
      {problems.length > 0 && (
        <ul className="algo-problems" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="algo-form-row">
        <button type="submit" className="btn" disabled={busy}>
          Make alert
        </button>
      </div>
    </form>
  );
}

function Webhook({ hook, onChange }: { hook: WebhookSettings | null; onChange: (h: WebhookSettings) => void }) {
  const { rest } = useApp();
  const [url, setUrl] = useState(hook?.url ?? "");
  const [secret, setSecret] = useState<string | null>(null); // shown this once
  const [result, setResult] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = (work: Promise<void>) => {
    setBusy(true);
    work.catch((e: unknown) => setResult({ tone: "bad", text: errorText(e) })).finally(() => setBusy(false));
  };
  return (
    <form
      className="algo-form"
      aria-label="Webhook"
      onSubmit={(e) => {
        e.preventDefault();
        run(
          rest.put<WebhookSettings>("/api/alerts/webhook", { url }).then((h) => {
            setSecret(h.secret ?? null);
            setResult({ tone: "good", text: "Saved" });
            onChange({ url: h.url, set: true });
          }),
        );
      }}
    >
      <p className="dim">
        Alerts marked "Webhook" are also POSTed to this https address, signed: <code>X-MockTrade-Signature: sha256=HMAC(secret, "&lt;X-MockTrade-Timestamp&gt;.&lt;body&gt;")</code>. Only public addresses are allowed.
      </p>
      <div className="algo-form-row">
        <div className="algo-field wide">
          <span>Address</span>
          <input aria-label="Webhook address" value={url} placeholder="https://…" onChange={(e) => setUrl(e.target.value)} />
        </div>
        <button type="submit" className="btn small" disabled={busy || !url}>
          {hook?.set ? "Replace (new secret)" : "Save"}
        </button>
        {hook?.set && (
          <>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() =>
                run(
                  rest.post<{ ok: boolean; error: string }>("/api/alerts/webhook/test", {}).then((r) =>
                    setResult(r.ok ? { tone: "good", text: "Test message delivered" } : { tone: "bad", text: `Test failed: ${r.error}` }),
                  ),
                )
              }
            >
              Test
            </button>
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() =>
                run(
                  rest.delete("/api/alerts/webhook").then(() => {
                    setSecret(null);
                    setUrl("");
                    setResult({ tone: "good", text: "Removed" });
                    onChange({ url: null, set: false });
                  }),
                )
              }
            >
              Remove
            </button>
          </>
        )}
      </div>
      {secret && (
        <div className="webhook-secret" role="status">
          Your secret, shown only now: <code>{secret}</code>{" "}
          <button type="button" className="btn small" onClick={() => void navigator.clipboard?.writeText(secret)}>
            Copy
          </button>
        </div>
      )}
      {result && (
        <div className={`tone-${result.tone}`} role="alert">
          {result.text}
        </div>
      )}
    </form>
  );
}

registerWidget<AlertsParams>({
  kind: "alerts",
  title: "Alerts",
  description: "Your price, % move, volume, OI and spread alerts; in-app and by webhook",
  component: AlertsManager,
  defaults: () => ({ link: "pink", mode: "list" }),
  link: "drives",
});
