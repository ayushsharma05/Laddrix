// Sign in: username, password and the 6-digit authenticator code. The server never says
// which part was wrong, and locks the user after repeated failures.

import { useState, type FormEvent } from "react";
import { ApiError } from "../api/rest";
import { useApp } from "../app/context";

export function Login() {
  const { session } = useApp();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const brokerLogin = new URLSearchParams(location.search).get("broker_login");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await session.login(username.trim(), password, code);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Sign in failed");
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <section className="window login-box" aria-labelledby="login-title">
        <div className="window-bar">
          <span id="login-title" className="strong">Laddrix · Sign in</span>
        </div>
        <form className="login-form" onSubmit={submit}>
          <label className="field">
            Username
            <input
              className="input"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={64}
              required
            />
          </label>
          <label className="field">
            Password
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={256}
              required
            />
          </label>
          <label className="field">
            Authenticator code
            <input
              className="input code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              required
            />
          </label>
          {error && (
            <div role="alert" className="alert bad">
              {error}
            </div>
          )}
          {brokerLogin === "failed" && !error && (
            <div role="status" className="alert warn">
              The Zerodha login didn't finish. Sign in and try again from the account menu.
            </div>
          )}
          <button type="submit" className="btn primary" disabled={busy || code.length !== 6}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <p className="hint">Never says which part was wrong. Repeated failures lock the account for 15 minutes.</p>
        </form>
      </section>
    </main>
  );
}
