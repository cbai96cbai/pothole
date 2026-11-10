import { dateTime, usd } from "../format";
import type { IncidentItem } from "../types";
import { ACTION_WORDS, DRILL, PROPOSED_NOT_JUDGED } from "../wording";

export function IncidentList({ items, empty }: { items: IncidentItem[]; empty: string }) {
  if (items.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ol className="timeline">
      {items.map((i) => (
        <li key={i.run_id}>
          <div className="when">{dateTime(i.started)}</div>
          <div>
            {i.repair.drill && <span className="badge drill">{DRILL}</span>}{" "}
            <strong>{ACTION_WORDS[i.repair.action] ?? i.repair.action}</strong>
            {i.repair.not_judged && <span className="badge pending">{PROPOSED_NOT_JUDGED}</span>}
            {i.repair.verdict && <span className="badge">Judged: {i.repair.verdict}</span>}
          </div>
          <p>
            Run status: {i.status.replace(/_/g, " ")}.{" "}
            {i.failed_checks.length > 0 && (
              <>Failed: {i.failed_checks.map((c) => `${c.name} (${c.detail})`).join("; ")}. </>
            )}
            Cost {usd(i.ai_cost)}.
          </p>
          {i.repair.url ? (
            <a href={i.repair.url} rel="noopener">
              {i.repair.action === "patch" ? "The pull request" : "The issue"}
            </a>
          ) : (
            <span className="muted">No pull request or issue was opened.</span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function Incidents({ items }: { items: IncidentItem[] }) {
  return (
    <>
      <h1>Incidents</h1>
      <p>
        Real failures of the daily load, and what the repair step did about each. A proposal on a
        real failure is {PROPOSED_NOT_JUDGED.toLowerCase()}: no judge has scored it, and a person
        decides whether to merge it.
      </p>
      <IncidentList items={items} empty="No real failures so far." />
    </>
  );
}
