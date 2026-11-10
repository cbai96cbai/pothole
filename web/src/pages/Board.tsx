import { dateTime, num, pct, usd } from "../format";
import type { Backtest, Board as BoardData, NullSimulation } from "../types";
import { MONTHLY_LIMIT, NOTHING_NOTABLE, SCREEN_NOTE, STATUS_WORDS } from "../wording";

export function Board({
  board,
  backtest,
  nullSimulation,
}: {
  board: BoardData;
  backtest: Backtest;
  nullSimulation: NullSimulation;
}) {
  const run = board.last_run;
  const day = board.latest_day;
  return (
    <>
      <h1>Status</h1>
      <p className={`status status-${board.dataset_status}`}>
        {STATUS_WORDS[board.dataset_status] ?? board.dataset_status}
      </p>
      {run && (
        <p>
          Last run: {dateTime(run.finished)}. It loaded {num(run.rows_loaded)} rows in {run.pages}{" "}
          {run.pages === 1 ? "page" : "pages"}.
        </p>
      )}

      <h2>Checks, last result</h2>
      {board.checks.length === 0 ? (
        <p className="muted">No checks recorded yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr><th>Check</th><th>Result</th><th>Detail</th><th>Run finished</th></tr>
            </thead>
            <tbody>
              {board.checks.map((c) => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <td><span className={`badge level-${c.level}`}>{c.level}</span></td>
                  <td>{c.detail}</td>
                  <td>{dateTime(c.finished)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Latest analysed day</h2>
      {day ? (
        <p>
          <strong>{day.day}</strong>: {num(day.tested)} series tested, {day.cleared} cleared the bar,{" "}
          {day.findings === 0 ? NOTHING_NOTABLE.toLowerCase() : `${day.findings} investigated`}.{" "}
          {day.skipped_reason && <span>Skipped: {day.skipped_reason}. </span>}
          <a href="#/findings">All days</a>
        </p>
      ) : (
        <p className="muted">No day has been analysed yet.</p>
      )}

      <h2>How often the screen flags</h2>
      <p>{SCREEN_NOTE}</p>
      <ul>
        <li>
          Backtest, {backtest.days_tested} past days ({backtest.window[0]} to{" "}
          {backtest.window[backtest.window.length - 1]}): {backtest.flags_per_day.min} to{" "}
          {backtest.flags_per_day.max} series flagged a day, median {backtest.flags_per_day.median}.{" "}
          <span className="muted">{backtest.label}.</span>
        </li>
        <li>
          Simulated quiet days ({nullSimulation.days_tested} analysed of{" "}
          {nullSimulation.days_simulated_after_lead_in} simulated): the share of days on which the
          full rule ({nullSimulation.rule}) still flags at least one series, by how much more
          spread than Poisson noise the counts are:{" "}
          {Object.entries(nullSimulation.share_of_days_with_a_candidate)
            .map(([k, v]) => `${k} ${pct(v, 1)}`)
            .join(", ")}
          . <span className="muted">{nullSimulation.label}.</span>
        </li>
      </ul>

      <h2>Cost</h2>
      <p>
        This month: {usd(board.month_ai_cost)} of AI calls. The limit is {MONTHLY_LIMIT} a month, set
        by construction: each run is capped at a fixed number of calls with a fixed maximum price,
        and the run refuses to start above its share.
      </p>
    </>
  );
}
