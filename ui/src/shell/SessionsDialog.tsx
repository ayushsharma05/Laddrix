// Your own sessions (#363): every browser or app logged in as you, with when and from where,
// and an End button for each. A session you don't recognise: end it, then change your
// password (that ends the others too).
//
//   GET /api/me/sessions ─► newest first; "this one" can't be ended here (Log out does that)
//   End ─► DELETE /api/me/sessions/{id} ─► gone at once; its open screens close within seconds

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/rest";
import { useApp } from "../app/context";

export interface SessionRow {
  id: string;
  created_at: string;
  last_seen: string;
  device: string;
  ip: string;
  current: boolean;
}

export function SessionsDialog({ onClose }: { onClose: () => void }) {
  const { rest } = useApp();
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(await rest.get<SessionRow[]>("/api/me/sessions"));
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [rest]);
  useEffect(() => {
    let live = true; // the dialog may close before the answer comes
    rest.get<SessionRow[]>("/api/me/sessions").then(
      (found) => {
        if (!live) return;
        setRows(found);
        setError("");
      },
      (err: unknown) => live && setError(err instanceof ApiError ? err.message : String(err)),
    );
    return () => {
      live = false;
    };
  }, [rest]);

  const end = async (id: string) => {
    try {
      await rest.delete(`/api/me/sessions/${encodeURIComponent(id)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
    await load();
  };

  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <section
        className="window dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sessions-title"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      >
        <div className="window-bar">
          <span id="sessions-title" className="strong grow">
            Your sessions
          </span>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="dialog-body">
          <p className="dim">Don't recognise one? End it, then change your password.</p>
          {rows === null && !error && <p className="dim">Loading…</p>}
          {rows && (
            <table className="grid">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>From</th>
                  <th>Logged in</th>
                  <th>Last seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id}>
                    <td title={s.device}>{browserName(s.device)}</td>
                    <td>{s.ip || "—"}</td>
                    <td>{when(s.created_at)}</td>
                    <td>{when(s.last_seen)}</td>
                    <td>
                      {s.current ? (
                        <span className="dim">this one</span>
                      ) : (
                        <button type="button" className="btn small" onClick={() => void end(s.id)}>
                          End
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {error && (
            <div role="alert" className="alert bad">
              {error}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

/** A short name for a browser's user-agent text ("Chrome on Windows"); the full text is the
 * cell's tooltip. */
export function browserName(agent: string): string {
  if (!agent) return "unknown";
  const app = /Electron\//.test(agent)
    ? "MockTrade desktop"
    : /Edg\//.test(agent)
      ? "Edge"
      : /Firefox\//.test(agent)
        ? "Firefox"
        : /Chrome\//.test(agent)
          ? "Chrome"
          : /Safari\//.test(agent)
            ? "Safari"
            : (agent.split("/")[0] ?? agent);
  const os = /Windows/.test(agent)
    ? "Windows"
    : /Android/.test(agent)
      ? "Android"
      : /iPhone|iPad/.test(agent)
        ? "iOS"
        : /Mac OS X/.test(agent)
          ? "macOS"
          : /Linux/.test(agent)
            ? "Linux"
            : "";
  return os ? `${app} on ${os}` : app;
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}
