import { Trace } from "../components/Trace";
import { PointsMap } from "../components/PointsMap";
import { SeriesChart } from "../components/SeriesChart";
import { callText, date, oneDecimal, small, titleCase, usd } from "../format";
import type { Finding as FindingData } from "../types";
import {
  EVENTS_CUT_SHORT,
  EVENTS_EMPTY,
  EVENTS_NOTE,
  EVENTS_UNAVAILABLE,
  SCREEN_NOTE,
  UNFINISHED,
  WEATHER_SOURCE_WORDS,
  WEATHER_UNAVAILABLE,
} from "../wording";

export function Finding({ finding: f }: { finding: FindingData }) {
  // the banner already says the investigation did not finish
  const notes = f.notes.filter((n) => !n.startsWith("unfinished:"));
  return (
    <>
      <p><a href="#/findings">All findings</a></p>
      <h1>
        {titleCase(f.complaint_type)}, {f.borough} community district {f.board}
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
        <div><dt>Expected</dt><dd>{oneDecimal(f.expected)}</dd></div>
        <div><dt>Ratio</dt><dd>{oneDecimal(f.ratio)}x</dd></div>
        <div><dt>Screen score q</dt><dd>{small(f.q)}</dd></div>
      </dl>
      <p className="muted">{SCREEN_NOTE}</p>

      <h2>Daily counts</h2>
      <SeriesChart series={f.series} highlight={f.day} expected={f.expected} dataStarts={f.data_starts} />

      <h2>Where</h2>
      <PointsMap points={f.points} />

      <h2>Weather and events</h2>
      {f.weather ? (
        <p>
          Central Park that day: rain {f.weather.precip_mm ?? "n/a"} mm, high{" "}
          {f.weather.tmax_c ?? "n/a"} °C (Open-Meteo, {WEATHER_SOURCE_WORDS[f.weather.source] ?? f.weather.source}).
        </p>
      ) : (
        <p className="muted">{WEATHER_UNAVAILABLE}</p>
      )}
      <p className="muted">{EVENTS_NOTE}</p>
      {f.events_state === "unavailable" && <p className="notice">{EVENTS_UNAVAILABLE}</p>}
      {f.events_state === "complete" && f.events.length === 0 && <p>{EVENTS_EMPTY}</p>}
      {f.events.length > 0 && (
        <ul>
          {f.events.map((e, i) => (
            <li key={i}>{e.name} ({e.type}), {date(e.start)} to {date(e.end)}</li>
          ))}
        </ul>
      )}
      {f.events_state === "cut_short" && <p className="notice">{EVENTS_CUT_SHORT}</p>}

      {f.status === "investigated" && (
        <>
          <h2>What the summary relied on</h2>
          {f.evidence.length === 0 ? (
            <p className="muted">No evidence recorded.</p>
          ) : (
            <ul>
              {f.evidence.map((e, i) => (
                <li key={i}><code>{callText(e.tool, e.args)}</code>: {e.excerpt}</li>
              ))}
            </ul>
          )}
        </>
      )}

      <details className="dev">
        <summary>Trace (for developers)</summary>
        <p className="muted">Every model call and tool call behind the summary, as recorded. Cost {usd(f.cost)}.</p>
        <Trace trace={f.trace} />
      </details>
    </>
  );
}
