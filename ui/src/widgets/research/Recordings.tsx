// Recordings: the days of ticks this server has kept (K11.1), for replays and backtests.
//
//   Kept 30 days                                                        Refresh
//   Day          Ticks       Size     Instruments   Segments
//   2026-10-16   1,23,456    10.4 MB  42            NFO 9.1 MB · NSE 1.3 MB
//   ▾ (picked) NIFTY26OCTFUT 45,210 · RELIANCE 3,210 · …

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/rest";
import type { RecordedDay, RecordedDayDetail, Recordings as RecordingsData } from "../../api/types";
import { useApp } from "../../app/context";
import { qty, shortName } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { bytesText } from "./model";

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function Recordings({ publish }: WidgetProps<BaseParams>) {
  const { rest } = useApp();
  const [data, setData] = useState<RecordingsData | null>(null);
  const [off, setOff] = useState<string | null>(null); // this server doesn't record
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<RecordedDayDetail | null>(null);
  const [loads, setLoads] = useState(0);

  useEffect(() => {
    let live = true;
    rest
      .get<RecordingsData>("/api/research/recordings")
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setOff(e.message);
        else setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [rest, loads]);

  useEffect(() => {
    if (!open) return; // (another day's detail is never shown: the rows check its day)
    let live = true;
    rest
      .get<RecordedDayDetail>(`/api/research/recordings/${open}`)
      .then((d) => live && setDetail(d))
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest, open, loads]);

  const refresh = useCallback(() => setLoads((n) => n + 1), []);

  if (off) return <div className="empty-note">Not recording: {off} (LADDRIX_RECORD_DAYS sets how many days are kept).</div>;
  return (
    <div className="book research">
      <div className="book-bar">
        {data && <span className="dim">{data.keep_days ? `Kept ${data.keep_days} days` : "Kept until deleted"}</span>}
        <span className="book-spacer" />
        <button type="button" className="btn small" onClick={refresh}>
          Refresh
        </button>
      </div>
      <div className="book-scroll">
        {error && (
          <div className="empty-note tone-bad" role="alert">
            {error}
          </div>
        )}
        <div className="grid-head book-row rec-row" role="row">
          <span>Day</span>
          <span className="num">Ticks</span>
          <span className="num">Size</span>
          <span className="num">Instruments</span>
          <span>Segments</span>
        </div>
        <div className="grid-body" role="grid" aria-label="Recorded days">
          {data && !data.days.length && <div className="empty-note">Nothing recorded yet: ticks are kept from the next market tick on.</div>}
          {data?.days.map((d) => (
            <div key={d.day}>
              <div role="row" aria-expanded={open === d.day} className={`row book-row rec-row${open === d.day ? " selected" : ""}`} onClick={() => setOpen(open === d.day ? null : d.day)}>
                <span>{d.day}</span>
                <span className="num">{qty(d.ticks)}</span>
                <span className="num">{bytesText(d.bytes)}</span>
                <span className="num">{d.instruments}</span>
                <span className="dim">
                  {Object.entries(d.segments)
                    .map(([seg, b]) => `${seg} ${bytesText(b)}`)
                    .join(" · ")}
                </span>
              </div>
              {open === d.day && detail?.day === d.day && (
                <>
                  <ul className="rec-instruments" aria-label={`Instruments on ${d.day}`}>
                    {Object.entries(detail.instruments)
                      .sort((a, b) => b[1] - a[1])
                      .map(([id, n]) => (
                        <li key={id} onClick={() => publish(id)} title="Show in linked widgets">
                          {shortName(id)} <span className="dim">{qty(n)}</span>
                        </li>
                      ))}
                  </ul>
                  <DayActions
                    day={d}
                    onDeleted={() => {
                      setOpen(null);
                      refresh();
                    }}
                  />
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Under an opened day: how to replay it (a replay is its own server, so practice never mixes
 *  with live) and, for an admin, delete it after a confirmation. */
function DayActions({ day: d, onDeleted }: { day: RecordedDay; onDeleted: () => void }) {
  const { rest } = useApp();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  if (!d.replay && !d.deletable) return null;
  const remove = () => {
    setBusy(true);
    rest
      .delete(`/api/research/recordings/${d.day}`)
      .then(onDeleted)
      .catch((e: unknown) => setProblem(errorText(e)))
      .finally(() => {
        setBusy(false);
        setArmed(false);
      });
  };
  const copy = () => {
    navigator.clipboard
      ?.writeText(d.replay!)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };
  return (
    <div className="rec-actions" aria-label={`Actions for ${d.day}`}>
      {d.replay && (
        <span className="rec-replay" title="Run this on the server: a replay starts as its own server with its own data">
          Replay: <code>{d.replay}</code>
          <button type="button" className="btn small" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </span>
      )}
      <span className="book-spacer" />
      {d.deletable &&
        (armed ? (
          <button type="button" className="btn small danger" disabled={busy} onClick={remove}>
            Confirm: delete {d.day} ({bytesText(d.bytes)})
          </button>
        ) : (
          <button type="button" className="btn small" onClick={() => setArmed(true)}>
            Delete…
          </button>
        ))}
      {problem && (
        <span className="tone-bad" role="alert">
          {problem}
        </span>
      )}
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "recordings",
  title: "Recordings",
  description: "Days of recorded ticks kept for replays and backtests: ticks, size, instruments",
  component: Recordings,
  defaults: () => ({ link: "pink" }),
  link: "drives",
});
