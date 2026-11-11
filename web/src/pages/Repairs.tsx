import { pct } from "../format";
import type { RepairSummary, Repairs as RepairsData } from "../types";
import { NOT_YET_RUN, decidedBy } from "../wording";

const EXPECTED: Record<string, string> = {
  patch: "propose a fix",
  issue: "open an issue",
  no_change: "leave unchanged",
  nothing: "nothing to do",
};
const rate = (r: { rate: number | null } | null) => (r ? pct(r.rate) : "n/a");
const frac = (r: { correct: number; of: number } | null) => (r ? `${r.correct} of ${r.of}` : "n/a");

function Table({ rows, split }: { rows: RepairSummary[]; split: string }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Repairer</th><th>Cases</th><th>Repairs that worked</th><th>Right to open an issue</th>
            <th>Right to change nothing</th><th>Fault noticed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.repairer}>
              <td>{s.repairer === "baseline" ? "Rule-based fixer" : "Agent"}</td>
              <td>{s.cases}</td>
              <td>{rate(s.repair_success)} ({frac(s.repair_success)})</td>
              <td>{rate(s.issue_accuracy)} ({frac(s.issue_accuracy)})</td>
              <td>{rate(s.no_change_accuracy)} ({frac(s.no_change_accuracy)})</td>
              <td>{s.detection_coverage_fault_cases.detected} of {s.detection_coverage_fault_cases.of}</td>
            </tr>
          ))}
          {!rows.some((r) => r.repairer !== "baseline") && (
            <tr>
              <td>Agent</td>
              <td colSpan={5} className="muted">{NOT_YET_RUN} on the {split} split</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Families({ rows }: { rows: RepairSummary[] }) {
  const names = [...new Set(rows.flatMap((r) => Object.keys(r.per_family)))];
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Fault family</th><th>Right answer</th>
            {rows.map((r) => <th key={r.repairer}>{r.repairer === "baseline" ? "Rule-based fixer" : "Agent"}</th>)}
          </tr>
        </thead>
        <tbody>
          {names.map((n) => (
            <tr key={n}>
              <td>{n.replace(/_/g, " ")}</td>
              <td>{(EXPECTED[rows[0]?.per_family[n]?.expected ?? ""] ?? rows[0]?.per_family[n]?.expected) ?? ""}</td>
              {rows.map((r) => {
                const f = r.per_family[n];
                return <td key={r.repairer}>{f ? `${f.correct} of ${f.cases}` : "n/a"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Repairs({ repairs }: { repairs: RepairsData }) {
  const splits = [...new Set(repairs.summaries.map((s) => s.split))];
  return (
    <>
      <h1>Repairs</h1>
      <p>
        Each case is a <strong>Drill</strong>: a known fault planted in recorded data, then handed to
        a repairer, which must propose a fix, open an issue, or leave things alone. A judge then
        checks the result automatically against a clean reference. The rule-based fixer uses fixed
        rules and no AI; it is the baseline the agent has to beat. The cases are split into a
        development set and a test set.
      </p>
      {!repairs.agent_run && (
        <p className="notice">Agent results: {NOT_YET_RUN}. Only the rule-based fixer is shown.</p>
      )}
      {splits.map((split) => {
        const rows = repairs.summaries.filter((s) => s.split === split);
        return (
          <section key={split}>
            <h2>{split === "test" ? "Test split" : "Development split"}</h2>
            <Table rows={rows} split={split} />
            <h3>By fault family</h3>
            <Families rows={rows} />
            {rows.map((r) => (
              <p key={r.repairer} className="muted">
                {r.repairer === "baseline" ? "Rule-based fixer" : "Agent"}, cases decided by:{" "}
                {Object.entries(r.decided_by ?? {})
                  .map(([by, n]) => `${decidedBy(by)} ${n}`)
                  .join(", ") || "not recorded"}
                .
              </p>
            ))}
            {rows.map((r) => (
              <details key={r.repairer}>
                <summary>
                  {r.repairer === "baseline" ? "Rule-based fixer" : "Agent"}: {r.failures.length} cases not
                  handled correctly
                </summary>
                <ul>
                  {r.failures.map((f) => (
                    <li key={f.id}>{f.id}: {f.reason}</li>
                  ))}
                </ul>
              </details>
            ))}
          </section>
        );
      })}
    </>
  );
}
