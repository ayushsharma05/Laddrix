// Admin: users, broker accounts and each account's risk limits, without the server's terminal
// (K13.2; the API is K13.1, docs/design/admin.md). Admins change; risk users only read. Every
// rule (the last admin, nobody turns themselves off, ranges, what loosens a limit) is the
// server's: this screen shows its answers in its own words.
//
//   [Users] Accounts  Limits                                      + New user
//   ayush   Ayush Sharma   admin trader   on · 1 signed in   Roles…  Turn off…  Reset password…
//   ┌ shown once ─────────────────────────┐
//   │ temporary password  ▒▒▒▒  [QR]       │ give both to the user; they set their own at the
//   │ authenticator key   ABCD EFGH …      │ first sign-in
//   └──────────────────────────────────────┘
//
// Dangerous changes take a second click: turning a user or an account off, a new password or
// authenticator, and any limit change the server marks as looser.

import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { AdminAccount, AdminAccounts, AdminUser, AlgoParamSpec, LimitsAnswer, LimitsSaved, LimitsSpec, RoleName, UserCreated } from "../../api/types";
import { useApp, useNow, useStore } from "../../app/context";
import { QrCode } from "../../components/QrCode";
import { SearchBox } from "../../components/SearchBox";
import { shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { accountState, limitGroups, limitInputs, parseLimits, totpParts, userState, type LimitInput, type LimitInputs } from "./model";

export interface AdminParams extends BaseParams {
  tab?: "users" | "accounts" | "limits";
  accountId?: string; // the Limits tab's account
}

interface Role {
  name: RoleName;
  help: string;
}

/** What a create or reset handed out, shown once. */
interface Secret {
  title: string;
  password?: string;
  totpUri?: string;
  note: string;
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const TABS: [NonNullable<AdminParams["tab"]>, string][] = [
  ["users", "Users"],
  ["accounts", "Accounts"],
  ["limits", "Limits"],
];

export function Admin({ params, setParams }: WidgetProps<AdminParams>) {
  const roles = useStore((s) => s.me?.user.roles ?? []);
  const canChange = roles.includes("admin");
  const canRead = canChange || roles.includes("risk");
  const tab = params.tab ?? "users";
  if (!canRead) return <div className="empty-note">For admin and risk users.</div>;
  return (
    <div className="book admin">
      <div className="book-bar">
        {TABS.map(([code, label]) => (
          <button key={code} type="button" className={`book-tab${tab === code ? " on" : ""}`} aria-pressed={tab === code} onClick={() => setParams({ tab: code })}>
            {label}
          </button>
        ))}
        <span className="book-spacer" />
        {!canChange && <span className="dim">read only (risk)</span>}
      </div>
      {tab === "users" && <Users canChange={canChange} />}
      {tab === "accounts" && <Accounts canChange={canChange} onLimits={(id) => setParams({ tab: "limits", accountId: id })} />}
      {tab === "limits" && <Limits canChange={canChange} accountId={params.accountId} pick={(id) => setParams({ accountId: id })} />}
    </div>
  );
}

// --- users ------------------------------------------------------------------------------------

function Users({ canChange }: { canChange: boolean }) {
  const { rest } = useApp();
  const me = useStore((s) => s.me?.user.id);
  const now = useNow(30_000);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [secret, setSecret] = useState<Secret | null>(null);
  const [adding, setAdding] = useState(false);
  const [armed, setArmed] = useState<string | null>(null); // "<action>|<user>" waiting for its second click
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(() => {
    rest
      .get<AdminUser[]>("/api/admin/users")
      .then(setUsers)
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest]);
  useEffect(() => {
    load();
    rest
      .get<Role[]>("/api/admin/roles")
      .then(setRoles)
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest, load]);

  const act = (run: () => Promise<unknown>) => {
    setProblem(null);
    setArmed(null);
    run()
      .then(load)
      .catch((e: unknown) => setProblem(errorText(e)));
  };
  const second = (key: string, label: string, run: () => Promise<unknown>, danger = true) =>
    armed === key ? (
      <button type="button" className={`btn small${danger ? " danger" : ""}`} onClick={() => act(run)}>
        Confirm {label.toLowerCase()}
      </button>
    ) : (
      <button type="button" className="btn small" onClick={() => setArmed(key)}>
        {label}…
      </button>
    );

  if (!users) return problem ? <div className="empty-note tone-bad">{problem}</div> : <div className="empty-note">loading…</div>;
  return (
    <>
      {problem && (
        <div className="empty-note tone-bad" role="alert">
          {problem}
        </div>
      )}
      {secret && <SecretPanel secret={secret} onDone={() => setSecret(null)} />}
      {canChange && !adding && !secret && (
        <div className="admin-actions">
          <button type="button" className="btn small" onClick={() => setAdding(true)}>
            + New user
          </button>
        </div>
      )}
      {adding && (
        <NewUser
          roles={roles}
          onCancel={() => setAdding(false)}
          onMade={(made) => {
            setAdding(false);
            setSecret({ title: `${made.user.id} added`, password: made.temporary_password, totpUri: made.totp_uri, note: made.note });
            load();
          }}
        />
      )}
      <div className="grid-head book-row admin-user-row" role="row">
        <span>User</span>
        <span>Name</span>
        <span>Roles</span>
        <span>State</span>
        <span />
      </div>
      <div className="grid-body" role="grid" aria-label="Users">
        {users.map((u) => {
          const state = userState(u, now);
          return (
            <div key={u.id} role="row" className={`row book-row admin-user-row${u.active ? "" : " dim"}`}>
              <span className="strong">{u.id}</span>
              <span>{u.name}</span>
              <span>{editing === u.id ? <RoleEdit roles={roles} have={u.roles} onSave={(next) => act(() => rest.patch(`/api/admin/users/${u.id}`, { roles: next }).finally(() => setEditing(null)))} onCancel={() => setEditing(null)} /> : u.roles.join(" ")}</span>
              <span className={`tone-${state.tone}`}>{state.text}</span>
              <span className="admin-row-actions">
                {canChange && editing !== u.id && (
                  <>
                    <button type="button" className="btn small" onClick={() => setEditing(u.id)}>
                      Roles…
                    </button>
                    {u.active
                      ? u.id !== me && second(`off|${u.id}`, "Turn off", () => rest.patch(`/api/admin/users/${u.id}`, { active: false }))
                      : <button type="button" className="btn small" onClick={() => act(() => rest.patch(`/api/admin/users/${u.id}`, { active: true }))}>Turn on</button>}
                    {(u.locked_until || u.failures > 0) && (
                      <button type="button" className="btn small" onClick={() => act(() => rest.post(`/api/admin/users/${u.id}/unlock`, {}))}>
                        Unlock
                      </button>
                    )}
                    {second(`password|${u.id}`, "New password", () =>
                      rest.post<{ temporary_password: string; note: string }>(`/api/admin/users/${u.id}/reset-password`, {}).then((r) => setSecret({ title: `New password for ${u.id}`, password: r.temporary_password, note: r.note })),
                    )}
                    {second(`totp|${u.id}`, "New authenticator", () =>
                      rest.post<{ totp_uri: string; note: string }>(`/api/admin/users/${u.id}/reset-authenticator`, {}).then((r) => setSecret({ title: `New authenticator for ${u.id}`, totpUri: r.totp_uri, note: r.note })),
                    )}
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}

function RoleEdit({ roles, have, onSave, onCancel }: { roles: Role[]; have: RoleName[]; onSave: (next: RoleName[]) => void; onCancel: () => void }) {
  const [picked, setPicked] = useState<RoleName[]>(have);
  return (
    <span className="admin-roles" role="group" aria-label="Roles">
      {roles.map((r) => (
        <label key={r.name} title={r.help}>
          <input type="checkbox" checked={picked.includes(r.name)} onChange={(e) => setPicked((now) => (e.target.checked ? [...now, r.name] : now.filter((x) => x !== r.name)))} /> {r.name}
        </label>
      ))}
      <button type="button" className="btn small" disabled={!picked.length} onClick={() => onSave(picked)}>
        Save
      </button>
      <button type="button" className="btn small" onClick={onCancel}>
        Cancel
      </button>
    </span>
  );
}

function NewUser({ roles, onMade, onCancel }: { roles: Role[]; onMade: (made: UserCreated) => void; onCancel: () => void }) {
  const { rest } = useApp();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<RoleName[]>(["trader"]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    rest
      .post<UserCreated>("/api/admin/users", { id: id.trim(), name: name.trim(), roles: picked })
      .then(onMade)
      .catch((err: unknown) => setProblem(errorText(err)))
      .finally(() => setBusy(false));
  };
  return (
    <form className="admin-form" aria-label="New user" onSubmit={submit}>
      <label>
        User id <input aria-label="User id" value={id} onChange={(e) => setId(e.target.value)} maxLength={40} required />
      </label>
      <label>
        Name <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
      </label>
      <span className="admin-roles" role="group" aria-label="Roles">
        {roles.map((r) => (
          <label key={r.name} title={r.help}>
            <input type="checkbox" checked={picked.includes(r.name)} onChange={(e) => setPicked((now) => (e.target.checked ? [...now, r.name] : now.filter((x) => x !== r.name)))} /> {r.name}
          </label>
        ))}
      </span>
      {problem && (
        <div className="tone-bad" role="alert">
          {problem}
        </div>
      )}
      <span className="admin-actions">
        <button type="submit" className="btn small" disabled={busy || !picked.length}>
          Add user
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Cancel
        </button>
      </span>
    </form>
  );
}

/** A one-time password and/or authenticator link, shown once (the server keeps only hashes). */
function SecretPanel({ secret, onDone }: { secret: Secret; onDone: () => void }) {
  const totp = secret.totpUri ? totpParts(secret.totpUri) : null;
  return (
    <section className="admin-secret" role="region" aria-label="Shown once">
      <div className="strong">{secret.title}: shown once</div>
      <div className="admin-secret-body">
        <div>
          {secret.password && (
            <p>
              One-time password <code aria-label="One-time password">{secret.password}</code>
            </p>
          )}
          {totp && (
            <p>
              Authenticator key <code aria-label="Authenticator key">{totp.secret}</code>
              <span className="dim"> ({totp.issuer}: {totp.account})</span>
            </p>
          )}
          <p className="dim">{secret.note}</p>
        </div>
        {secret.totpUri && <QrCode text={secret.totpUri} label="Authenticator QR code" />}
      </div>
      <button type="button" className="btn small" onClick={onDone}>
        Done: I've handed these over
      </button>
    </section>
  );
}

// --- accounts ---------------------------------------------------------------------------------

function Accounts({ canChange, onLimits }: { canChange: boolean; onLimits: (id: string) => void }) {
  const { rest } = useApp();
  const [data, setData] = useState<AdminAccounts | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const load = useCallback(() => {
    rest
      .get<AdminAccounts>("/api/admin/accounts")
      .then(setData)
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest]);
  useEffect(() => {
    load();
    rest
      .get<AdminUser[]>("/api/admin/users")
      .then(setUsers)
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest, load]);
  const act = (run: () => Promise<unknown>) => {
    setProblem(null);
    setArmed(null);
    run()
      .then(load)
      .catch((e: unknown) => setProblem(errorText(e)));
  };
  if (!data) return problem ? <div className="empty-note tone-bad">{problem}</div> : <div className="empty-note">loading…</div>;
  return (
    <>
      {problem && (
        <div className="empty-note tone-bad" role="alert">
          {problem}
        </div>
      )}
      {canChange && !adding && (
        <div className="admin-actions">
          <button type="button" className="btn small" onClick={() => setAdding(true)} disabled={!data.brokers.length} title={data.brokers.length ? "" : "this server can't add accounts"}>
            + New account
          </button>
        </div>
      )}
      {adding && (
        <NewAccount
          brokers={data.brokers}
          users={users}
          onCancel={() => setAdding(false)}
          onMade={() => {
            setAdding(false);
            load();
          }}
        />
      )}
      <div className="grid-head book-row admin-account-row" role="row">
        <span>Account</span>
        <span>Broker</span>
        <span>Owner</span>
        <span>Also trades it</span>
        <span>State</span>
        <span />
      </div>
      <div className="grid-body" role="grid" aria-label="Accounts">
        {data.accounts.map((a) => {
          const state = accountState(a);
          return (
            <div key={a.id} role="row" className={`row book-row admin-account-row${a.active ? "" : " dim"}`}>
              <span className="strong">{a.id}</span>
              <span>
                {a.broker} <span className="dim">{a.broker_client_id}</span>
              </span>
              <span>{a.owner}</span>
              <span>{editing === a.id ? <TraderEdit users={users} account={a} onSave={(next) => act(() => rest.patch(`/api/admin/accounts/${a.id}`, { traders: next }).finally(() => setEditing(null)))} onCancel={() => setEditing(null)} /> : a.traders.join(", ") || "–"}</span>
              <span className={`tone-${state.tone}`}>{state.text}</span>
              <span className="admin-row-actions">
                <button type="button" className="btn small" onClick={() => onLimits(a.id)}>
                  Limits
                </button>
                {canChange && editing !== a.id && (
                  <>
                    <button type="button" className="btn small" onClick={() => setEditing(a.id)}>
                      Traders…
                    </button>
                    {a.active ? (
                      armed === a.id ? (
                        <button type="button" className="btn small danger" onClick={() => act(() => rest.patch(`/api/admin/accounts/${a.id}`, { active: false }))}>
                          Confirm turn off
                        </button>
                      ) : (
                        <button type="button" className="btn small" onClick={() => setArmed(a.id)} title="Only closing trades from then on; cancels always work">
                          Turn off…
                        </button>
                      )
                    ) : (
                      <button type="button" className="btn small" onClick={() => act(() => rest.patch(`/api/admin/accounts/${a.id}`, { active: true }))}>
                        Turn on
                      </button>
                    )}
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}

function TraderEdit({ users, account, onSave, onCancel }: { users: AdminUser[]; account: AdminAccount; onSave: (next: string[]) => void; onCancel: () => void }) {
  const [picked, setPicked] = useState<string[]>(account.traders);
  return (
    <span className="admin-roles" role="group" aria-label="Traders">
      {users
        .filter((u) => u.id !== account.owner)
        .map((u) => (
          <label key={u.id}>
            <input type="checkbox" checked={picked.includes(u.id)} onChange={(e) => setPicked((now) => (e.target.checked ? [...now, u.id] : now.filter((x) => x !== u.id)))} /> {u.id}
          </label>
        ))}
      <button type="button" className="btn small" onClick={() => onSave(picked)}>
        Save
      </button>
      <button type="button" className="btn small" onClick={onCancel}>
        Cancel
      </button>
    </span>
  );
}

function NewAccount({ brokers, users, onMade, onCancel }: { brokers: string[]; users: AdminUser[]; onMade: () => void; onCancel: () => void }) {
  const { rest } = useApp();
  const [id, setId] = useState("");
  const [broker, setBroker] = useState(brokers[0] ?? "");
  const [clientId, setClientId] = useState("");
  const [owner, setOwner] = useState(users[0]?.id ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    rest
      .post<AdminAccount>("/api/admin/accounts", { id: id.trim(), broker, broker_client_id: clientId.trim(), owner, traders: [] })
      .then(onMade)
      .catch((err: unknown) => setProblem(errorText(err)))
      .finally(() => setBusy(false));
  };
  return (
    <form className="admin-form" aria-label="New account" onSubmit={submit}>
      <label>
        Account id <input aria-label="Account id" value={id} onChange={(e) => setId(e.target.value)} maxLength={40} required />
      </label>
      <label>
        Broker
        <select aria-label="Broker" value={broker} onChange={(e) => setBroker(e.target.value)}>
          {brokers.map((b) => (
            <option key={b}>{b}</option>
          ))}
        </select>
      </label>
      <label>
        Client id <input aria-label="Client id" value={clientId} onChange={(e) => setClientId(e.target.value)} maxLength={40} required />
      </label>
      <label>
        Owner
        <select aria-label="Owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
          {users.map((u) => (
            <option key={u.id}>{u.id}</option>
          ))}
        </select>
      </label>
      {problem && (
        <div className="tone-bad" role="alert">
          {problem}
        </div>
      )}
      <span className="admin-actions">
        <button type="submit" className="btn small" disabled={busy || !owner || !broker}>
          Add account
        </button>
        <button type="button" className="btn small" onClick={onCancel}>
          Cancel
        </button>
      </span>
    </form>
  );
}

// --- limits -----------------------------------------------------------------------------------

function Limits({ canChange, accountId, pick }: { canChange: boolean; accountId?: string; pick: (id: string) => void }) {
  const { rest } = useApp();
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [spec, setSpec] = useState<LimitsSpec | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([rest.get<AdminAccounts>("/api/admin/accounts"), rest.get<LimitsSpec>("/api/admin/limits/spec")])
      .then(([a, s]) => {
        setAccounts(a.accounts);
        setSpec(s);
      })
      .catch((e: unknown) => setProblem(errorText(e)));
  }, [rest]);
  if (!accounts || !spec) return problem ? <div className="empty-note tone-bad">{problem}</div> : <div className="empty-note">loading…</div>;
  const chosen = accountId && accounts.some((a) => a.id === accountId) ? accountId : accounts[0]?.id;
  return (
    <>
      <div className="admin-actions">
        <label>
          Account{" "}
          <select aria-label="Account" value={chosen ?? ""} onChange={(e) => pick(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id}>{a.id}</option>
            ))}
          </select>
        </label>
        <span className="dim">{spec.help}</span>
      </div>
      {chosen ? <LimitsForm key={chosen} spec={spec} accountId={chosen} canChange={canChange} /> : <div className="empty-note">No accounts yet.</div>}
    </>
  );
}

function LimitsForm({ spec, accountId, canChange }: { spec: LimitsSpec; accountId: string; canChange: boolean }) {
  const { rest } = useApp();
  const [inputs, setInputs] = useState<LimitInputs | null>(null);
  const [preview, setPreview] = useState<LimitsSaved | null>(null);
  const [saved, setSaved] = useState<LimitsSaved | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const path = `/api/admin/accounts/${encodeURIComponent(accountId)}/limits`;
  const load = useCallback(() => {
    rest
      .get<LimitsAnswer>(path)
      .then((a) => setInputs(limitInputs(spec, a.limits)))
      .catch((e: unknown) => setProblems([errorText(e)]));
  }, [rest, path, spec]);
  useEffect(load, [load]);

  const edit = (name: string, v: LimitInput) => {
    setInputs((now) => (now ? { ...now, [name]: v } : now));
    setPreview(null); // what is saved must be what was previewed
    setSaved(null);
    setArmed(false);
  };
  const check = () => {
    if (!inputs) return;
    const { limits, problems: found } = parseLimits(spec, inputs);
    setProblems(found);
    setSaved(null);
    if (!limits) return;
    setBusy(true);
    rest
      .put<LimitsSaved>(`${path}?dry_run=true`, { limits })
      .then(setPreview)
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };
  const save = () => {
    if (!preview) return;
    setBusy(true);
    rest
      .put<LimitsSaved>(path, { limits: preview.limits })
      .then((r) => {
        setSaved(r);
        setPreview(null);
        setArmed(false);
        setInputs(limitInputs(spec, r.limits));
      })
      .catch((e: unknown) => setProblems([errorText(e)]))
      .finally(() => setBusy(false));
  };

  if (!inputs) return problems.length ? <div className="empty-note tone-bad">{problems[0]}</div> : <div className="empty-note">loading…</div>;
  const looser = preview?.changes.filter((c) => c.looser).length ?? 0;
  return (
    <div className="admin-limits">
      <div className="admin-limit-grid" role="group" aria-label={`Limits of ${accountId}`}>
        {limitGroups(spec).map((g) => (
          <fieldset key={g.title || g.params[0]!.name} className={g.title ? "admin-group" : "admin-plain"}>
            {g.title && <legend>{g.title}</legend>}
            {g.params.map((p) => (
              <LimitField key={p.name} param={p} value={inputs[p.name]} onChange={(v) => edit(p.name, v)} disabled={!canChange} short={!!g.title} />
            ))}
          </fieldset>
        ))}
      </div>
      {problems.length > 0 && (
        <ul className="tone-bad" role="alert">
          {problems.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {preview && (
        <section className="admin-preview" aria-label="What changes">
          {!preview.changes.length ? (
            <p>Nothing changes.</p>
          ) : (
            <ul>
              {preview.changes.map((c) => (
                <li key={c.name} className={c.looser ? "tone-pending" : ""}>
                  {c.text}
                  {c.looser && <span className="strong"> · looser</span>}
                </li>
              ))}
            </ul>
          )}
          {preview.warnings.map((w) => (
            <p key={w} className="tone-bad">
              {w}
            </p>
          ))}
        </section>
      )}
      {saved && <p className="tone-good" role="status">{saved.applied ? "Saved: the limits apply from the next order." : "Nothing changed."}</p>}
      {canChange && (
        <div className="admin-actions">
          <button type="button" className="btn small" onClick={check} disabled={busy}>
            Check changes
          </button>
          {preview && preview.changes.length > 0 &&
            (looser && !armed ? (
              <button type="button" className="btn small" onClick={() => setArmed(true)}>
                Save… ({looser} looser)
              </button>
            ) : (
              <button type="button" className={`btn small${looser ? " danger" : ""}`} onClick={save} disabled={busy}>
                {looser ? "Confirm: loosen and save" : "Save"}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

function LimitField({ param: p, value, onChange, disabled, short }: { param: AlgoParamSpec; value: LimitInput | undefined; onChange: (v: LimitInput) => void; disabled: boolean; short: boolean }) {
  const label = short && p.label.includes(":") ? p.label.split(":").slice(1).join(":").trim() : p.label;
  if (p.kind === "flag") {
    return (
      <label className="admin-field flag" title={p.help}>
        <input type="checkbox" aria-label={p.label} checked={value === true} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {label}
      </label>
    );
  }
  if (p.kind === "choices") {
    const list = Array.isArray(value) ? value : [];
    return (
      <div className="admin-field wide" title={p.help} role="group" aria-label={p.label}>
        <span>{label}</span>
        {p.choices.map((c) => (
          <label key={c}>
            <input type="checkbox" checked={list.includes(c)} disabled={disabled} onChange={(e) => onChange(e.target.checked ? [...list, c] : list.filter((x) => x !== c))} /> {c}
          </label>
        ))}
      </div>
    );
  }
  if (p.kind === "instruments") {
    const list = Array.isArray(value) ? value : [];
    return (
      <div className="admin-field wide" title={p.help} role="group" aria-label={p.label}>
        <span>{label}</span>
        {list.map((id) => (
          <span key={id} className="algo-picked">
            {shortName(id)}
            {!disabled && (
              <button type="button" className="row-action" aria-label={`Remove ${id}`} onClick={() => onChange(list.filter((x) => x !== id))}>
                ×
              </button>
            )}
          </span>
        ))}
        {!list.length && <span className="dim">none</span>}
        {!disabled && <SearchBox placeholder="Add a contract…" ariaLabel={`Add to ${p.label}`} onPick={(i) => onChange(list.includes(i.id) ? list : [...list, i.id])} />}
      </div>
    );
  }
  const unit = p.kind === "money" ? "₹" : p.kind === "percent" ? "%" : p.kind === "seconds" ? "s" : p.kind === "lots" ? "lots" : "";
  return (
    <label className="admin-field" title={p.help}>
      <span>{label}</span>
      <span className="algo-control">
        {p.kind === "money" && <span className="dim">₹</span>}
        <input aria-label={p.label} inputMode={p.kind === "money" || p.kind === "percent" || p.kind === "seconds" ? "decimal" : "numeric"} value={typeof value === "string" ? value : ""} size={p.kind === "money" ? 12 : 5} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        {unit && unit !== "₹" && <span className="dim">{unit}</span>}
      </span>
    </label>
  );
}

registerWidget<AdminParams>({
  kind: "admin",
  title: "Admin",
  description: "Users, roles, broker accounts and each account's risk limits",
  component: Admin,
  defaults: () => ({ link: "none", tab: "users" }),
  link: "none",
  roles: ["admin", "risk"],
  size: { width: 900, height: 520 }, // three tabs of forms: room to read them (or pop it out)
});
