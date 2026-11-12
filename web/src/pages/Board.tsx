import { Counts } from "../components/Counts";
import { dateTime, money, num, pct, usd } from "../format";
import type { Backtest, Board as BoardData, Facts, NullSimulation } from "../types";
import { NOTHING_NOTABLE, SCREEN_NOTE, STATUS_WORDS } from "../wording";

export function Board({
  board,
  backtest,
  nullSimulation,
  facts,
}: {
  board: BoardData;
  backtest: Backtest;
  nullSimulation: NullSimulation;
  facts: Facts;
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
      <p className="muted">
        Each check shows its most recent result. A result stays until a later run re-checks it, so an
        old failure can sit under a healthy status; the run date is in the last column.
      </p>
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
          <strong>{day.day}</strong>: <Counts tested={day.tested} cleared={day.cleared} />, {day.findings === 0 ? NOTHING_NOTABLE.toLowerCase() : `${day.findings} investigated`}.{" "}
          {day.skipped_reason && <span>Skipped: {day.skipped_reason}. </span>}
          <a href="#/findings">All days</a>
        </p>
      ) : (
        <p className="muted">No day has been analysed yet.</p>
      )}

      <h2>How often the screen flags</h2>
      <p>{SCREEN_NOTE}</p>
      <h3>On past days</h3>
      <p>
        Over {backtest.days_tested} past days ({backtest.days[0]?.day} to{" "}
        {backtest.days[backtest.days.length - 1]?.day}) the screen flagged between{" "}
        {backtest.flags_per_day.min} and {backtest.flags_per_day.max} series a day, median{" "}
        {backtest.flags_per_day.median}. <span className="muted">{backtest.label}.</span>
      </p>
      <h3>On simulated quiet days</h3>
      <p>
        {nullSimulation.days_tested} simulated days with no events were run through the same
        screen. A real day's counts are noisier than a coin-flip model predicts, so the table
        shows how the share of quiet days that still get a flag grows with the noise.{" "}
        <span className="muted">{nullSimulation.label}.</span>
      </p>
      <div className="scroll">
        <table className="rates">
          <thead>
            <tr><th>Noise, as a multiple of the simple model</th><th>Quiet days with at least one flag</th></tr>
          </thead>
          <tbody>
            {Object.entries(nullSimulation.share_of_days_with_a_candidate)
              .sort(([a], [b]) => parseFloat(a) - parseFloat(b))
              .map(([k, v]) => (
                <tr key={k}><td>{k}</td><td>{pct(v, 1)}</td></tr>
              ))}
          </tbody>
        </table>
      </div>

      <h2>Cost</h2>
      <p>
        This month: {usd(board.month_ai_cost)} of AI calls. The limit is {money(facts.monthly_limit)} a
        month, set by construction: each run is capped at {facts.calls_per_run} calls with a fixed
        maximum price, and the run refuses to start above {money(facts.run_ceiling)}, the limit
        divided across {facts.ceiling_days} days.
      </p>
    </>
  );
}
