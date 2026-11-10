import { Trace } from "../components/Trace";
import { PointsMap } from "../components/PointsMap";
import { SeriesChart } from "../components/SeriesChart";
import { small, usd } from "../format";
import type { Finding as FindingData } from "../types";
import { SCREEN_NOTE, UNFINISHED } from "../wording";

export function Finding({ finding: f }: { finding: FindingData }) {
  // the banner already says the investigation did not finish
  const notes = f.notes.filter((n) => !n.startsWith("unfinished:"));
  return (
    <>
      <p><a href="#/findings">All findings</a></p>
      <h1>
        {f.complaint_type}, {f.borough} community district {f.board}
      </h1>
      <p className="muted">{f.day}</p>
      {f.status === "unfinished" ? (
        <p className="notice">{UNFINISHED}</p>
      ) : (
        <p>{f.summary}</p>
      )}
      {notes.length > 0 && <p className="muted">Not computed: {notes.join("; ")}.</p>}
      <dl className="facts">
        <div><dt>Filed</dt><dd>{f.observed}</dd></div>
        <div><dt>Expected</dt><dd>{f.expected.toFixed(1)}</dd></div>
        <div><dt>Ratio</dt><dd>{f.ratio.toFixed(1)}x</dd></div>
        <div><dt>Screen score q</dt><dd>{small(f.q)}</dd></div>
      </dl>
      <p className="muted">{SCREEN_NOTE}</p>

      <h2>Daily counts</h2>
      <SeriesChart series={f.series} highlight={f.day} expected={f.expected} />

      <h2>Where</h2>
      <PointsMap points={f.points} />

      <h2>Weather and events</h2>
      {f.weather ? (
        <p>
          Central Park that day: rain {f.weather.precip_mm ?? "n/a"} mm, high{" "}
          {f.weather.tmax_c ?? "n/a"} °C (Open-Meteo {f.weather.source}).
        </p>
      ) : (
        <p className="muted">No weather was recorded.</p>
      )}
      {f.events.length === 0 ? (
        <p className="muted">No permitted city events overlapped the day.</p>
      ) : (
        <ul>
          {f.events.map((e, i) => (
            <li key={i}>{e.name} ({e.type}), {e.start.slice(0, 10)} to {e.end.slice(0, 10)}</li>
          ))}
        </ul>
      )}

      {f.status === "investigated" && (
        <>
          <h2>What the summary relied on</h2>
          {f.evidence.length === 0 ? (
            <p className="muted">No evidence recorded.</p>
          ) : (
            <ul>
              {f.evidence.map((e, i) => (
                <li key={i}><code>{e.tool}</code>: {e.excerpt}</li>
              ))}
            </ul>
          )}
        </>
      )}

      <h2>Trace</h2>
      <p className="muted">Every model call and tool call behind the summary. Cost {usd(f.cost)}.</p>
      <Trace trace={f.trace} />
    </>
  );
}
