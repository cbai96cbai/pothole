import type { ReactNode } from "react";

import { date } from "../format";
import type { Finding } from "../types";
import { EVENTS_CUT_SHORT, WEATHER_SOURCE_WORDS } from "../wording";

type Weather = NonNullable<Finding["weather"]>;
type EventRow = Finding["events"][number];

/** The weather sentence, used for the Weather section and for weather evidence. */
export function WeatherText({ weather: w }: { weather: Weather }) {
  return (
    <>
      Central Park that day: rain {w.precip_mm ?? "n/a"} mm, high {w.tmax_c ?? "n/a"} °C (Open-Meteo,{" "}
      {WEATHER_SOURCE_WORDS[w.source] ?? w.source}).
    </>
  );
}

export function EventItems({ events }: { events: EventRow[] }) {
  return (
    <ul>
      {events.map((e, i) => (
        <li key={i}>{e.name} ({e.type}), {date(e.start)} to {date(e.end)}</li>
      ))}
    </ul>
  );
}

function parse(excerpt: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(excerpt);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** A weather or events evidence excerpt in plain words, or null when it is not in that shape.
 * A shortened excerpt keeps its "first N of M" and omission notices. */
export function contextEvidence(tool: string, excerpt: string): ReactNode {
  const data = parse(excerpt);
  if (data === null) return null;
  if (tool === "weather" && "source" in data) {
    return <WeatherText weather={data as unknown as Weather} />;
  }
  if (tool === "events" && Array.isArray(data.events)) {
    const events = data.events as EventRow[];
    return (
      <>
        {events.length > 0 && <EventItems events={events} />}
        {events.length === 0 && !data.shown && <>No permitted event listing this district was returned.</>}
        {typeof data.shown === "string" && <p className="muted">Showing {data.shown}.</p>}
        {typeof data.omitted === "string" && <p className="muted">Omitted: {data.omitted}.</p>}
        {data.truncated === true && <p className="muted">{EVENTS_CUT_SHORT}</p>}
      </>
    );
  }
  return null;
}
