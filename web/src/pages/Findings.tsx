import { num, small } from "../format";
import { findingHref } from "../routes";
import type { FindingsIndex } from "../types";
import { NOTHING_NOTABLE, SCREEN_NOTE } from "../wording";

export function Findings({ index }: { index: FindingsIndex }) {
  return (
    <>
      <h1>Findings</h1>
      <p>{SCREEN_NOTE}</p>
      {index.days.length === 0 && <p className="muted">No day has been analysed yet.</p>}
      {index.days.map((d) => (
        <section key={d.day} className="day">
          <h2>{d.day}</h2>
          <p className="muted">
            {num(d.tested)} series tested, {d.cleared} cleared the bar.
            {d.skipped_reason ? ` Skipped: ${d.skipped_reason}.` : ""}
          </p>
          {d.findings.length === 0 ? (
            <p className="quiet">{NOTHING_NOTABLE}</p>
          ) : (
            d.findings.map((f) => (
              <article key={f.id} className="card">
                <h3>
                  <a href={findingHref(f.id)}>
                    {f.complaint_type}, {f.borough} board {f.board}
                  </a>
                </h3>
                <p>
                  {f.observed} filed against {f.expected.toFixed(1)} expected ({f.ratio.toFixed(1)}x,
                  screen score q = {small(f.q)}).
                </p>
                <p>{f.summary}</p>
              </article>
            ))
          )}
        </section>
      ))}
    </>
  );
}
