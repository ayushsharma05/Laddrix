// A fresh authenticator code for a privileged change (#363). The server answers 428
// "step-up: ..." when a session hasn't given a code in the last 5 minutes; Rest asks here,
// sends the code, and repeats the change once:
//
//   change ─► 428 ─► this dialog: "Enter a fresh authenticator code to <what>"
//                     ─► Confirm ─► POST /api/me/step-up ─► wrong: said here, ask again
//                                                        ─► right: the change is sent again
//                     ─► Cancel ─► the change fails with the server's reason
//
// STOP, Flatten and cancels never get here: the server never asks for them.

import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/rest";
import { useApp } from "../app/context";

interface Asking {
  reason: string;
  confirm: (code: string) => Promise<void>;
  done: (ok: boolean) => void;
}

export function StepUpDialog() {
  const { rest } = useApp();
  const [asking, setAsking] = useState<Asking | null>(null);

  useEffect(
    () => rest.askStepUpWith((reason, confirm) => new Promise<boolean>((done) => setAsking({ reason, confirm, done }))),
    [rest],
  );

  if (!asking) return null;
  const finish = (ok: boolean) => {
    asking.done(ok);
    setAsking(null);
  };
  return <CodeForm asking={asking} onFinish={finish} />;
}

function CodeForm({ asking, onFinish }: { asking: Asking; onFinish: (ok: boolean) => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  const send = async () => {
    if (busy || !/^\d{6}$/.test(code)) {
      setError("Enter the 6 digits your authenticator app shows now");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await asking.confirm(code);
      onFinish(true);
    } catch (err) {
      setCode("");
      setError(err instanceof ApiError && err.status === 403 ? "Wrong code: wait for the next one and try again" : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" onMouseDown={() => onFinish(false)}>
      <section
        className="window dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="step-up-title"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onFinish(false);
          if (e.key === "Enter") {
            e.preventDefault();
            void send();
          }
        }}
      >
        <div className="window-bar">
          <span id="step-up-title" className="strong grow">
            Confirm it's you
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={() => onFinish(false)}>
            ×
          </button>
        </div>
        <div className="dialog-body">
          <p>{sentence(asking.reason)}</p>
          <p className="dim">Asked for changes that add risk or give access; one code covers 5 minutes.</p>
          <label className="field">
            Authenticator code
            <input
              ref={input}
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
          </label>
          {error && (
            <div role="alert" className="alert bad">
              {error}
            </div>
          )}
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => onFinish(false)}>
              Cancel
            </button>
            <button type="button" className="btn primary" disabled={busy} onClick={() => void send()}>
              Confirm
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

/** The server's words ("enter a fresh authenticator code to …") as a sentence. */
function sentence(reason: string): string {
  const text = reason.charAt(0).toUpperCase() + reason.slice(1);
  return /[.!?]$/.test(text) ? text : `${text}.`;
}
