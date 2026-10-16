// Chart: candles with a volume pane, the live forming candle, the account's fills as marks,
// and click-a-price to fill in the linked ticket (K12.2; the data is K12.1, docs/design/charts.md).
//
//   [1m 3m 5m 15m 30m 1h 1d]  NIFTY26OCTFUT  O 25,010.00 H … L … C … V 1.2K  · live from 11:28
//   ┌──────────────────────────────────────────────────┬──────────┐
//   │  candles (up hollow, down filled) ▲ buys ▼ sells │ 25,020.00│  price axis, last price
//   │  volume                                          │          │
//   └──────────────────────────────────────────────────┴──────────┘
//    09:15        10:15        11:15        10-17                    time axis (dates at new days)
//
// Drag or ←/→ to pan (older candles load as you near the left), wheel or +/− to zoom, Home or
// a double-click for the newest. Click a price: the linked ticket takes it (and asks to send).
// Intervals come from the server (GET /api/charts/spec); nothing about them is coded here.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Candle, CandlesAnswer, ChartMark, ChartSpec } from "../../api/types";
import { useApp, useInstrument, useStore } from "../../app/context";
import { SearchBox } from "../../components/SearchBox";
import { clock, price, shortName, shortVolume } from "../../lib/format";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";
import { AXIS_W, drawChart, priceAtY, readColours } from "./draw";
import { DEFAULT_VIEW, indexAt, indiaParts, marksByCandle, mergeForming, mergeOlder, needsOlder, pan, snapTick, visible, zoom, type View } from "./model";

export interface ChartParams extends BaseParams {
  interval?: string;
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
let specCache: Promise<ChartSpec> | null = null; // the spec doesn't change while the app runs

export function Chart(props: WidgetProps<ChartParams>) {
  const id = props.params.instrumentId;
  if (!id) {
    return (
      <div className="ladder-pick">
        <div className="empty-note">Pick a contract for this chart.</div>
        <SearchBox placeholder="Search product…" ariaLabel="Chart instrument" onPick={(i) => props.setInstrument(i.id)} />
      </div>
    );
  }
  return <ChartBody key={id} {...props} instrumentId={id} />;
}

function ChartBody({ params, setParams, instrumentId: id }: WidgetProps<ChartParams> & { instrumentId: string }) {
  const { rest, stream, links } = useApp();
  const instrument = useInstrument(id);
  const accountId = useStore((s) => s.accountId);
  const turnover = useStore((s) => (s.accountId ? (s.books[s.accountId]?.charges?.turnover_paise ?? 0) : 0)); // changes with every fill
  const [spec, setSpec] = useState<ChartSpec | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [answer, setAnswer] = useState<Pick<CandlesAnswer, "live_from" | "note"> | null>(null);
  const [marks, setMarks] = useState<ChartMark[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(DEFAULT_VIEW);
  const [hover, setHover] = useState<{ i: number | null; y: number | null }>({ i: null, y: null });
  const [size, setSize] = useState({ width: 640, height: 320 });
  // older pages: only after the first load has answered (never two history reads at once)
  const [olderState, setOlderState] = useState<"wait" | "idle" | "loading" | "done">("wait");
  const [hint, setHint] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; moved: boolean; offset: number } | null>(null);
  const dragged = useRef(false); // the click that ends a drag picks nothing
  const load = useRef(0); // which load an answer belongs to: an interval change starts another

  useEffect(() => {
    let live = true;
    specCache ??= rest.get<ChartSpec>("/api/charts/spec");
    specCache.then((s) => live && setSpec(s)).catch((e: unknown) => {
      specCache = null;
      if (live) setError(errorText(e));
    });
    return () => {
      live = false;
    };
  }, [rest]);

  const interval = spec?.intervals.find((i) => i.code === params.interval) ?? spec?.intervals.find((i) => i.code === "5m") ?? spec?.intervals[0];
  const code = interval?.code;
  const base = `/api/charts/${encodeURIComponent(id)}/candles`;

  // the candles: a fresh load for each instrument and interval
  useEffect(() => {
    if (!code) return;
    let live = true;
    load.current += 1;
    setCandles([]);
    setView(DEFAULT_VIEW);
    setOlderState("wait");
    rest
      .get<CandlesAnswer>(`${base}?interval=${code}`)
      .then((a) => {
        if (!live) return;
        setCandles((now) => mergeOlder(a.candles, now)); // a stream candle may have come first
        setOlderState("idle");
        setAnswer({ live_from: a.live_from, note: a.note });
        setError(null);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [rest, base, code]);

  // the forming candle, live
  useEffect(() => {
    if (!code) return;
    return stream.subscribeCandles(id, code, (c) => setCandles((list) => mergeForming(list, c)));
  }, [stream, id, code]);

  // the account's fills as marks (again after every fill)
  const firstT = candles[0]?.t;
  useEffect(() => {
    if (!accountId || !firstT) return;
    let live = true;
    rest
      .get<{ marks: ChartMark[] }>(`/api/accounts/${encodeURIComponent(accountId)}/charts/${encodeURIComponent(id)}/marks?start=${encodeURIComponent(firstT)}`)
      .then((m) => live && setMarks(m.marks))
      .catch(() => live && setMarks([])); // marks are a nicety: the chart stands without them
    return () => {
      live = false;
    };
  }, [rest, accountId, id, firstT, turnover]);

  // the plot's size follows the widget
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const r = entry!.contentRect;
      if (r.width > 0 && r.height > 0) setSize({ width: Math.floor(r.width), height: Math.floor(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plotW = Math.max(10, size.width - AXIS_W);

  // older candles as the view nears the left edge
  useEffect(() => {
    if (!interval || !candles.length || olderState !== "idle" || !needsOlder(view, candles.length, plotW)) return;
    setOlderState("loading");
    const mine = load.current;
    const end = candles[0]!.t;
    const start = new Date(Date.parse(end) - interval.default_days * 86_400_000).toISOString();
    rest
      .get<CandlesAnswer>(`${base}?interval=${interval.code}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)
      .then((a) => {
        if (load.current !== mine) return; // for an interval no longer shown
        const before = a.candles.filter((c) => c.t < end);
        setCandles((list) => mergeOlder(before, list)); // the view counts from the newest: it stays put
        setOlderState(before.length ? "idle" : "done");
      })
      .catch(() => load.current === mine && setOlderState("done"));
  }, [rest, base, interval, candles, view, plotW, olderState]);

  const byCandle = useMemo(() => marksByCandle(candles, marks), [candles, marks]);
  const daily = (interval?.seconds ?? 0) >= 86_400;

  // paint
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return; // no canvas (tests): the readout still works
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size.width * dpr;
    canvas.height = size.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawChart(ctx, { width: size.width, height: size.height, candles, view, marks: byCandle, hover, daily, colours: readColours(boxRef.current), tick: instrument?.tick_paise });
  }, [candles, view, size, byCandle, hover, daily, instrument]);

  const pointer = (e: React.MouseEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const onMove = (e: React.MouseEvent) => {
    const { x, y } = pointer(e);
    const start = drag.current;
    if (start) {
      const bars = (x - start.x) / view.barW;
      if (Math.abs(bars) >= 1) start.moved = true;
      // `start`, not drag.current: the update runs later, after the drag may have ended
      setView((v) => pan({ ...v, offset: start.offset }, bars, candles.length, plotW));
      return;
    }
    setHover({ i: x <= plotW ? indexAt(x, view, candles.length, plotW) : null, y });
  };

  const onClick = (e: React.MouseEvent) => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    const { x, y } = pointer(e);
    if (x > plotW) return;
    const at = priceAtY(y, { width: size.width, height: size.height, candles, view });
    if (at === null || !instrument) return;
    const picked = snapTick(Math.round(at), instrument.tick_paise);
    if ((params.link ?? "none") === "none") {
      setHint("Link this chart to a ticket (the colour on its tab) to trade from it");
      return;
    }
    links.pickPrice(params.link, id, picked);
    setHint(`${price(picked)} sent to the linked ticket`);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const step = Math.max(1, Math.floor(plotW / view.barW / 4));
    if (e.key === "ArrowLeft") setView((v) => pan(v, step, candles.length, plotW));
    else if (e.key === "ArrowRight") setView((v) => pan(v, -step, candles.length, plotW));
    else if (e.key === "+" || e.key === "=") setView((v) => zoom(v, 1.25));
    else if (e.key === "-") setView((v) => zoom(v, 0.8));
    else if (e.key === "Home") setView(DEFAULT_VIEW);
    else return;
    e.preventDefault();
  };

  const shown = visible(view, candles.length, plotW);
  const focus = hover.i ?? (candles.length ? candles.length - 1 : null);
  const c = focus !== null ? candles[focus] : undefined;
  const marksHere = focus !== null ? (byCandle.get(focus) ?? []) : [];

  return (
    <div className="chart">
      <div className="book-bar chart-bar">
        <div className="chart-intervals" role="group" aria-label="Interval">
          {spec?.intervals.map((i) => (
            <button key={i.code} type="button" className={`book-tab${i.code === code ? " on" : ""}`} aria-pressed={i.code === code} title={i.label} onClick={() => setParams({ interval: i.code })}>
              {i.code}
            </button>
          ))}
        </div>
        <b>{instrument?.symbol ?? shortName(id)}</b>
        <span className="chart-readout" role="status" aria-label="Candle">
          {c ? (
            <>
              <span className="dim">{daily ? indiaParts(c.t).date : `${indiaParts(c.t).date.slice(5)} ${indiaParts(c.t).time}`}</span> O {price(c.o)} H {price(c.h)} L {price(c.l)} C{" "}
              <b className={c.c >= c.o ? "candle-up" : "candle-down"}>{price(c.c)}</b> V {shortVolume(c.v)}
              {c.oi !== null && ` OI ${shortVolume(c.oi)}`}
              {marksHere.map((m) => (
                <span key={`${m.client_order_id}-${m.t}`} className={m.side === "BUY" ? "buy-text" : "sell-text"} title={`${m.client_order_id} · ${m.strategy}`}>
                  {" "}
                  {m.side === "BUY" ? "▲" : "▼"} {m.side} {m.qty} @ {price(m.price_paise)} {clock(m.t, false)}
                </span>
              ))}
            </>
          ) : (
            <span className="dim">{error ? "" : "loading…"}</span>
          )}
        </span>
        <span className="book-spacer" />
        {answer?.note && (
          <span className="tone-bad" title={answer.note}>
            history unavailable
          </span>
        )}
        {olderState === "loading" && <span className="dim">loading older…</span>}
        {olderState === "wait" && candles.length > 0 && <span className="dim">loading history…</span>}
      </div>
      {error && (
        <div className="empty-note tone-bad" role="alert">
          {error}
        </div>
      )}
      {hint && <div className="chart-hint dim">{hint}</div>}
      <div ref={boxRef} className="chart-box-canvas">
        <canvas
          ref={canvasRef}
          role="img"
          tabIndex={0}
          aria-label={`${instrument?.symbol ?? id} ${code ?? ""} chart: ${shown.to - shown.from} of ${candles.length} candles${c ? `, last ${price(candles[candles.length - 1]!.c)}` : ""}`}
          style={{ width: "100%", height: "100%" }}
          onMouseMove={onMove}
          onMouseLeave={() => {
            drag.current = null;
            setHover({ i: null, y: null });
          }}
          onMouseDown={(e) => {
            drag.current = { x: pointer(e).x, moved: false, offset: view.offset };
          }}
          onMouseUp={() => {
            dragged.current = drag.current?.moved ?? false;
            drag.current = null;
          }}
          onClick={onClick}
          onDoubleClick={() => setView(DEFAULT_VIEW)}
          onWheel={(e) => setView((v) => zoom(v, e.deltaY < 0 ? 1.15 : 1 / 1.15))}
          onKeyDown={onKey}
        />
      </div>
    </div>
  );
}

registerWidget<ChartParams>({
  kind: "chart",
  title: "Chart",
  description: "Candles with volume, the live candle, your fills marked; click a price for the ticket",
  component: Chart,
  defaults: () => ({ link: "pink", interval: "5m" }),
  link: "follows",
  tabTitle: (p) => (p.instrumentId ? `${shortName(p.instrumentId)} ${p.interval ?? ""}` : undefined),
});

/** Forget the cached spec (tests). */
export const resetChartSpec = () => {
  specCache = null;
};
