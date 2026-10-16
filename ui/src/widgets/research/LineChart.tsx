// A small SVG line chart for research curves (equity over days, P&L through a day).
//
//   +₹1,200 ┤      ╱╲__╱‾‾          one 2 px line per series, a dashed zero line,
//        ₹0 ┼‾‾╲__╱                  the highest and lowest values on the left,
//     −₹300 ┤                        first and last x labels below; hover: a crosshair and
//           2026-10-13 … 2026-10-16  every series' value at that x (legend from 2 series)
//
// Colours are fixed slots (validated for colour-blind readers on the dark surface); the
// numbers are in text ink, never the series colour.

import { useMemo, useState } from "react";
import { signedRupees } from "../books/model";
import { nearest, plot, type Series } from "./model";

const W = 560;
const H = 160;
const PAD = 6;

export function LineChart({ title, xs, series, xText = (x) => x }: { title: string; xs: string[]; series: Series[]; xText?: (x: string) => string }) {
  const p = useMemo(() => plot(series, xs.length, W, H, PAD), [series, xs.length]);
  const [hover, setHover] = useState<number | null>(null);
  if (!xs.length) return <div className="empty-note">Nothing to draw yet.</div>;
  return (
    <figure className="line-chart" aria-label={title}>
      <figcaption>
        <span>{title}</span>
        {series.length > 1 && (
          <span className="chart-legend" aria-label="Legend">
            {series.map((s, i) => (
              <span key={s.key}>
                <i className={`swatch slot-${s.slot ?? i}`} aria-hidden="true" />
                {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <div className="chart-box">
        <div className="chart-y" aria-hidden="true">
          <span>{signedRupees(p.max)}</span>
          <span>{signedRupees(p.min)}</span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${title}: ${series.map((s) => `${s.label} ends at ${signedRupees(lastValue(s) ?? 0)}`).join("; ")}`}
          onMouseMove={(e) => {
            const box = e.currentTarget.getBoundingClientRect();
            const px = box.width ? ((e.clientX - box.left) * W) / box.width : 0;
            setHover(nearest(px, xs.length, W, PAD));
          }}
          onMouseLeave={() => setHover(null)}
        >
          {p.zero !== null && <line className="chart-zero" x1={0} x2={W} y1={p.zero} y2={p.zero} />}
          {p.paths.map((d, i) => d && <path key={series[i]!.key} className={`chart-line slot-${series[i]!.slot ?? i}`} d={d} vectorEffect="non-scaling-stroke" />)}
          {xs.length === 1 &&
            series.map((s, i) => s.points[0] != null && <circle key={s.key} className={`chart-dot slot-${s.slot ?? i}`} cx={p.x(0)} cy={p.y(s.points[0])} r={4} />)}
          {hover !== null && <line className="chart-cross" x1={p.x(hover)} x2={p.x(hover)} y1={0} y2={H} vectorEffect="non-scaling-stroke" />}
        </svg>
      </div>
      <div className="chart-x" aria-hidden="true">
        <span>{xText(xs[0]!)}</span>
        {xs.length > 1 && <span>{xText(xs[xs.length - 1]!)}</span>}
      </div>
      {hover !== null && (
        <div className="chart-tip" role="status">
          <b>{xText(xs[hover]!)}</b>
          {series.map((s, i) => (
            <span key={s.key}>
              <i className={`swatch slot-${s.slot ?? i}`} aria-hidden="true" />
              {series.length > 1 ? `${s.label} ` : ""}
              {s.points[hover] == null ? "–" : signedRupees(s.points[hover]!)}
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}

function lastValue(s: Series): number | null {
  for (let i = s.points.length - 1; i >= 0; i--) if (s.points[i] != null) return s.points[i]!;
  return null;
}
