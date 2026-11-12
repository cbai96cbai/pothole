import { Scroll } from "../components/Scroll";
import { money, num, pct } from "../format";
import type { Delay, Facts, NullSimulation } from "../types";
import { REPO_URL, SCREEN_NOTE } from "../wording";

export function About({
  delay,
  nullSimulation,
  facts,
}: {
  delay: Delay;
  nullSimulation: NullSimulation;
  facts: Facts;
}) {
  return (
    <>
      <h1>About</h1>
      <h2>What this is</h2>
      <p>
        Pothole is a personal portfolio project. Every day it loads the last {facts.window_days} days
        of public NYC 311 complaints and checks that the load looks right. When the city changes a
        field, an agent proposes a fix to the pipeline definition as a pull request that a person
        reviews and merges; it does not change anything by itself. It also looks for complaint types
        that are unusually busy in a community district. Code and history:{" "}
        <a href={REPO_URL}>the repository</a> (MIT license).
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
        Each day the analyst tests every combination of the {facts.complaint_types} most common
        complaint types and the {facts.districts} regular community districts, and an AI model
        investigates at most {facts.max_investigations} flagged series with a fixed set of read-only
        queries. Its summaries say what was associated with a spike. The measured behaviour of the
        screen is on the <a href="#/">status page</a>.
      </p>
      <p>
        Repair drills are different: a known fault is planted, the repairer answers, and the answer
        is judged automatically against a clean reference. Real failures are not judged; their
        proposals are labeled as such.
      </p>

      <h2>Schedule and late data</h2>
      <p>
        The pipeline runs once a day. It analyses the day {facts.analysis_lag_days} days before the
        run, because complaints arrive in the data after they are filed. The share of a day's
        complaints already present after each number of days is below. Only days old enough to have
        had that many days to arrive count at each step, so later steps rest on fewer rows.
      </p>
      <Scroll label="Share of a day's complaints present, by days after filing">
        <table>
          <thead>
            <tr><th>Days after filing</th>{Object.keys(delay.share_by_lag_days).map((k) => <th key={k}>{k}</th>)}</tr>
          </thead>
          <tbody>
            <tr>
              <td>Share present</td>
              {Object.values(delay.share_by_lag_days).map((v, i) => <td key={i}>{pct(v, 1)}</td>)}
            </tr>
            <tr>
              <td>Rows measured</td>
              {Object.values(delay.rows_by_lag_days).map((v, i) => <td key={i}>{num(v)}</td>)}
            </tr>
          </tbody>
        </table>
      </Scroll>
      <p className="muted">{delay.label}. A run can be missed or late; the last successful run date is at the top of every page.</p>
      <p className="muted">
        Quiet-day simulation: {nullSimulation.dispersion}. Rule tested: {nullSimulation.rule}.
      </p>

      <h2>Cost limits</h2>
      <p>
        AI spending is capped at {money(facts.monthly_limit)} a month by construction: each call has
        a maximum input and output size and so a maximum price ({money(facts.max_cost_per_call)}),
        each run has at most {facts.calls_per_run} calls ({money(facts.run_envelope)} at most), and
        the run refuses to start if that total is above {money(facts.run_ceiling)}, the limit
        divided across {facts.ceiling_days} days. Model: a small Azure OpenAI model at{" "}
        {money(facts.price_input_per_million)} per million input tokens and{" "}
        {money(facts.price_output_per_million)} per million output tokens.
      </p>
    </>
  );
}
