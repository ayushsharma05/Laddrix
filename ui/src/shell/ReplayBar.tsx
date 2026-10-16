// The replay bar: shown only on a replay server (K11.2), where prices come from a recorded day.
// Everything here comes from the server (GET /api/replay); the controls ask it (POST).
//
//   REPLAY 2026-10-16 · 10:42:15 · playing · [1× 10× 60× max] [Pause] [+15 min]

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/rest";
import { useApp, useNow } from "../app/context";
import { clock } from "../lib/format";

export interface ReplayStatus {
  day: string;
  state: "ready" | "playing" | "paused" | "finished";
  speed: number; // 0: as fast as possible
  at: string; // the replayed time
  fed: number;
  seeking_to: string | null;
}

export const SPEEDS: [number, string][] = [
  [1, "1×"],
  [10, "10×"],
  [60, "60×"],
  [0, "max"],
];

export function ReplayBar() {
  const { rest, store } = useApp();
  const now = useNow(1000);
  const [status, setStatus] = useState<ReplayStatus | null>(null);
  const [absent, setAbsent] = useState(false); // not a replay server: stop asking

  useEffect(() => {
    if (absent) return;
    let live = true;
    rest
      .get<ReplayStatus>("/api/replay")
      .then((s) => live && setStatus(s))
      .catch((e: unknown) => {
        if (live && e instanceof ApiError && e.status === 404) setAbsent(true);
      });
    return () => {
      live = false;
    };
  }, [rest, now, absent]);

  const control = useCallback(
    (body: Record<string, unknown>) => {
      rest
        .post<ReplayStatus>("/api/replay", body)
        .then(setStatus)
        .catch((e: unknown) => store.addMessage("WARN", "", `Replay: ${e instanceof Error ? e.message : String(e)}`));
    },
    [rest, store],
  );

  if (absent || !status) return null;
  const jumpTo = new Date(Date.parse(status.at) + 15 * 60_000).toISOString();
  return (
    <div className="replay-bar" role="region" aria-label="Replay">
      <b>REPLAY {status.day}</b>
      <span className="replay-time">{clock(status.at, false)}</span>
      <span className="dim">{status.seeking_to ? `jumping to ${clock(status.seeking_to, false)}…` : status.state}</span>
      <span className="replay-speeds">
        {SPEEDS.map(([speed, label]) => (
          <button
            key={label}
            type="button"
            className={`book-tab${status.speed === speed ? " on" : ""}`}
            aria-pressed={status.speed === speed}
            onClick={() => control({ action: "speed", speed })}
          >
            {label}
          </button>
        ))}
      </span>
      {status.state === "paused" ? (
        <button type="button" className="btn small" onClick={() => control({ action: "resume" })}>
          Resume
        </button>
      ) : (
        <button type="button" className="btn small" disabled={status.state !== "playing"} onClick={() => control({ action: "pause" })}>
          Pause
        </button>
      )}
      <button type="button" className="btn small" disabled={status.state === "finished"} onClick={() => control({ action: "seek", at: jumpTo })}>
        +15 min
      </button>
      <span className="dim">practice on a recorded day · nothing reaches the market</span>
    </div>
  );
}
