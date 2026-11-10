import { num, pct } from "../format";
import type { Delay, NullSimulation } from "../types";
import { MONTHLY_LIMIT, REPO_URL, SCREEN_NOTE } from "../wording";

export function About({ delay, nullSimulation }: { delay: Delay; nullSimulation: NullSimulation }) {
  return (
    <>
      <h1>About</h1>
      <h2>What this is</h2>
      <p>
        Pothole is a personal portfolio project. Every day it loads the last 90 days of public NYC 311
        complaints, checks that the load looks right, repairs its own pipeline definition when the
        city changes a field, and looks for complaint types that are unusually busy in a community
        district. Code and history: <a href={REPO_URL}>the repository</a> (MIT license).
      </p>

      <h2>Data sources and licenses</h2>
      <ul>
        <li>
          311 Service Requests (<code>erm2-nwe9</code>) and Permitted Event Information (
          <code>tvpp-9vvx</code>), NYC Open Data, <a href="https://data.cityofnewyork.us">data.cityofnewyork.us</a>.
          Used under the city's open data terms. The site shows counts, descriptor fields and
          complaint locations; it does not show addresses or free-text fields.
        </li>
        <li>
          Weather: <a href="https://open-meteo.com">Open-Meteo</a>, Central Park point, licensed CC BY 4.0.
        </li>
        <li>
          Map tiles: <a href="https://openfreemap.org">OpenFreeMap</a>, with data © OpenStreetMap
          contributors; drawn with MapLibre GL JS. These are the only third-party requests the page makes.
        </li>
      </ul>

      <h2>What the analysis claims</h2>
      <p>{SCREEN_NOTE}</p>
      <p>
        Each day the analyst tests every combination of the 25 most common complaint types and the 59
        regular community districts, and an AI model investigates at most two flagged series with a
        fixed set of read-only queries. Its summaries say what was associated with a spike. The
        measured behaviour of the screen is on the <a href="#/">status page</a>.
      </p>

      <h2>Schedule and late data</h2>
      <p>
        The pipeline runs once a day. It analyses the day two days before the run, because complaints
        arrive in the data after they are filed. Measured over {num(delay.rows_measured)} rows, the
        share of a day's complaints already present is:
      </p>
      <div className="scroll">
        <table>
          <thead>
            <tr><th>Days after filing</th>{Object.keys(delay.share_by_lag_days).map((k) => <th key={k}>{k}</th>)}</tr>
          </thead>
          <tbody>
            <tr>
              <td>Share present</td>
              {Object.values(delay.share_by_lag_days).map((v, i) => <td key={i}>{pct(v, 1)}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="muted">{delay.label}. A run can be missed or late; the last successful run date is at the top of every page.</p>
      <p className="muted">
        Quiet-day measurement: {nullSimulation.dispersion}. {nullSimulation.label}.
      </p>

      <h2>Cost limits</h2>
      <p>
        AI spending is capped at {MONTHLY_LIMIT} a month by construction: each call has a maximum input
        and output size and so a maximum price, each run has a fixed maximum number of calls, and the
        run refuses to start if that total is above one thirty-first of the monthly limit. Model: a
        small Azure OpenAI model at $0.25 per million input tokens and $2.00 per million output
        tokens.
      </p>
    </>
  );
}
