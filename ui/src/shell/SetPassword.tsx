// "Set your password": shown instead of the terminal when an admin made this user, or reset their
// password, with a one-time password (K13.2). Until the user sets their own, the server answers
// nothing else and the live stream stays shut. The password rules are the server's: its answer
// is shown as it comes.

import { useState, type FormEvent } from "react";
import { useApp, useStore } from "../app/context";

export function SetPassword() {
  const { session } = useApp();
  const user = useStore((s) => s.me?.user);
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const differs = again !== "" && again !== next;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || differs) return;
    setBusy(true);
    setError("");
    try {
      await session.changePassword(old, next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The password wasn't changed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <section className="window login-box" aria-labelledby="set-password-title">
        <div className="window-bar">
          <span id="set-password-title" className="strong">
            MockTrade · Set your password
          </span>
        </div>
        <form className="login-form" onSubmit={submit}>
          <p className="hint">
            {user?.name ?? user?.id}, you signed in with a one-time password from an admin. Choose your own to go on; your other sign-ins end.
          </p>
          <label className="field">
            One-time password
            <input className="input" type="password" autoComplete="current-password" autoFocus value={old} onChange={(e) => setOld(e.target.value)} maxLength={256} required />
          </label>
          <label className="field">
            New password
            <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} maxLength={256} required />
          </label>
          <label className="field">
            New password again
            <input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} maxLength={256} required />
          </label>
          {differs && (
            <div role="status" className="alert warn">
              The two new passwords differ.
            </div>
          )}
          {error && (
            <div role="alert" className="alert bad">
              {error}
            </div>
          )}
          <button type="submit" className="btn primary" disabled={busy || differs || !old || !next || !again}>
            {busy ? "Saving…" : "Set password"}
          </button>
          <button type="button" className="btn" onClick={() => void session.logout()}>
            Sign out
          </button>
        </form>
      </section>
    </main>
  );
}
