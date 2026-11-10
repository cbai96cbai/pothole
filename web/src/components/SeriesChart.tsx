import { useEffect, useRef, useState } from "react";

import { date } from "../format";

const H = 240;
const PAD = { l: 40, r: 12, t: 12, b: 32 };
const DEFAULT_WIDTH = 640;

export function SeriesChart({
  series,
  highlight,
  expected,
}: {
  series: [string, number][];
  highlight: string;
  expected: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(DEFAULT_WIDTH);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(240, Math.floor(el.getBoundingClientRect().width)));
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  if (series.length === 0) return <p className="muted">No daily counts recorded.</p>;
  const top = Math.max(...series.map(([, n]) => n), expected, 1);
  const x = (i: number) =>
    PAD.l + (series.length === 1 ? 0 : (i / (series.length - 1)) * (width - PAD.l - PAD.r));
  const y = (n: number) => PAD.t + (1 - n / top) * (H - PAD.t - PAD.b);
  const line = series.map(([, n], i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(n).toFixed(1)}`);
  const hi = series.findIndex(([d]) => d === highlight);
  const first = series[0];
  const last = series[series.length - 1];
  const marked = hi >= 0 ? series[hi] : undefined;
  return (
    <figure className="chart">
      <div ref={box}>
        <svg
          width={width}
          height={H}
          role="img"
          aria-label={`Daily complaint counts from ${first ? date(first[0]) : ""} to ${last ? date(last[0]) : ""}; ${highlight} is marked`}
        >
          <line className="axis" x1={PAD.l} x2={width - PAD.r} y1={y(0)} y2={y(0)} />
          <line className="axis" x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={y(0)} />
          <text className="tick" x={PAD.l - 6} y={y(0)} textAnchor="end">0</text>
          <text className="tick" x={PAD.l - 6} y={y(top) + 10} textAnchor="end">{Math.round(top)}</text>
          <line className="expected" x1={PAD.l} x2={width - PAD.r} y1={y(expected)} y2={y(expected)} />
          <text className="tick" x={width - PAD.r} y={y(expected) - 4} textAnchor="end">
            expected {expected.toFixed(1)}
          </text>
          <path className="line" d={line.join("")} />
          {marked && <circle className="mark" cx={x(hi)} cy={y(marked[1])} r={5} />}
          {first && <text className="tick" x={PAD.l} y={H - 8}>{date(first[0])}</text>}
          {last && <text className="tick" x={width - PAD.r} y={H - 8} textAnchor="end">{date(last[0])}</text>}
        </svg>
      </div>
      <figcaption className="muted">Complaints per day. The marked point is the flagged day.</figcaption>
    </figure>
  );
}
