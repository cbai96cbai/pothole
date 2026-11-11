import { pct } from "../format";
import type { Compare, DecidedRates, Family, Rate, RepairSummary, Repairs as RepairsData } from "../types";
import { NOT_YET_RUN, decidedBy } from "../wording";

const EXPECTED: Record<string, string> = {
  patch: "propose a fix",
  issue: "open an issue",
  no_change: "leave unchanged",
  nothing: "nothing to do",
};
const BASELINE = "baseline";
const repairerName = (repairer: string) => (repairer === BASELINE ? "Rule-based fixer" : "Agent");
const rate = (r: Rate, none: string) => (r.of === 0 ? none : `${pct(r.rate)} (${r.correct} of ${r.of})`);
const cell = (r: Rate | null, none: string) => (r ? rate(r, none) : none);
const signed = (x: number) => {
  const n = Math.round(x * 100);
  return n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0";
};

/** "Rule-based fixer" or "Agent"; a split with several runs of one repairer numbers them in order. */
function labelsOf(rows: RepairSummary[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const s of rows) {
    const same = rows.filter((r) => r.repairer === s.repairer);
    const name = repairerName(s.repairer);
    out[s.run_id] = same.length > 1 ? `${name} run ${same.indexOf(s) + 1}` : name;
  }
  return out;
}

const repairerCases = (s: RepairSummary) => (s.decided_by[s.repairer] ?? 0) + (s.decided_by.code ?? 0);

function RepairerTable({ rows, label }: { rows: RepairSummary[]; label: Record<string, string> }) {
  const none = "none (all decided by the fixed rule)";
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Repairer</th><th>Cases it decided</th><th>Repairs that worked</th>
            <th>Right to open an issue</th><th>Right to change nothing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.run_id}>
              <td>{label[s.run_id]}</td>
              <td>{repairerCases(s)}</td>
              <td>{cell(s.repairer_decided.repair_success, none)}</td>
              <td>{cell(s.repairer_decided.issue_accuracy, none)}</td>
              <td>{cell(s.repairer_decided.no_change_accuracy, none)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RuleTable({ rows, label }: { rows: RepairSummary[]; label: Record<string, string> }) {
  const rates = (d: DecidedRates) => [d.issue_accuracy, d.no_change_accuracy];
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Repairer</th><th>Cases</th><th>Right to open an issue</th><th>Right to change nothing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.run_id}>
              <td>{label[s.run_id]}</td>
              <td>{s.decided_by.rule ?? 0}</td>
              {rates(s.rule_decided).map((r, i) => <td key={i}>{cell(r, "none")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function familyCell(f: Family | undefined): string {
  if (!f) return "n/a";
  const mine = f.repairer_cases > 0 ? `${f.repairer_correct} of ${f.repairer_cases}` : "";
  const rule = f.rule_cases > 0 ? `rule ${f.rule_correct} of ${f.rule_cases}` : "";
  return [mine, rule].filter(Boolean).join(" + ") || "n/a";
}

function Families({ rows, label }: { rows: RepairSummary[]; label: Record<string, string> }) {
  const names = [...new Set(rows.flatMap((r) => Object.keys(r.per_family)))];
  const expected = (n: string) => rows.find((r) => r.per_family[n])?.per_family[n]?.expected ?? "";
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Fault family</th><th>Right answer</th>
            {rows.map((r) => <th key={r.run_id}>{label[r.run_id]}</th>)}
          </tr>
        </thead>
        <tbody>
          {names.map((n) => (
            <tr key={n}>
              <td>{n.replace(/_/g, " ")}</td>
              <td>{EXPECTED[expected(n)] ?? expected(n)}</td>
              {rows.map((r) => <td key={r.run_id}>{familyCell(r.per_family[n])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Comparison({ compare, rows }: { compare: Compare; rows: RepairSummary[] }) {
  const name = (id: string) =>
    repairerName(rows.find((r) => r.run_id === id)?.repairer ?? "").toLowerCase();
  const note = compare.note.charAt(0).toUpperCase() + compare.note.slice(1);
  const d = compare.repair_success;
  const [low, high] = d.interval_95;
  return (
    <p>
      {`Repairs that worked, ${name(compare.a)} minus ${name(compare.b)}: ${signed(d.difference)} points (95% interval ${signed(low)} to ${signed(high)}). `}
      {`The interval resamples whole fault families. ${note}${note.endsWith(".") ? "" : "."}`}
    </p>
  );
}

export function Repairs({ repairs }: { repairs: RepairsData }) {
  const splits = [...new Set(repairs.summaries.map((s) => s.split))];
  const hasBaseline = repairs.summaries.some((s) => s.repairer === BASELINE);
  const compare = repairs.compare;
  return (
    <>
      <h1>Repairs</h1>
      <p>
        Each case is a <strong>Drill</strong>: a known fault planted in recorded data, then handed to
        a repairer, which must propose a fix, open an issue, or leave things alone. A judge then
        checks the result automatically against a clean reference. The rule-based fixer uses fixed
        rules and no AI; it is the baseline the agent has to beat. The cases are split into a
        development set and a test set. When only the row count is off (a spike, or part of a day
        missing), a fixed rule decides, not either repairer, so those cases are scored separately.
      </p>
      {splits.length === 0 && <p className="notice">No measurement is published yet.</p>}
      {hasBaseline && !repairs.agent_run && (
        <p className="notice">Agent results: {NOT_YET_RUN}. Only the rule-based fixer is shown.</p>
      )}
      {splits.map((split) => {
        const rows = repairs.summaries.filter((s) => s.split === split);
        const label = labelsOf(rows);
        const checks = rows[0];
        const fault = checks?.detection_coverage_fault_cases;
        const benign = checks?.detection_coverage_benign_control;
        return (
          <section key={split}>
            <h2>{split === "test" ? "Test split" : "Development split"}</h2>
            <h3>Decided by the repairer</h3>
            <RepairerTable rows={rows} label={label} />
            <h3>Decided by the fixed volume rule, no AI</h3>
            <RuleTable rows={rows} label={label} />
            {fault && benign && (
              <p className="muted">
                {`The checks noticed the planted fault in ${fault.detected} of ${fault.of} fault cases and flagged ${benign.detected} of ${benign.of} harmless changes.`}
              </p>
            )}
            {split === "test" && compare && <Comparison compare={compare} rows={repairs.summaries} />}
            <h3>By fault family</h3>
            <Families rows={rows} label={label} />
            {rows.map((r) => (
              <details key={r.run_id}>
                <summary>
                  {label[r.run_id]}: {r.failures.length} cases not handled correctly
                </summary>
                <ul>
                  {r.failures.map((f) => (
                    <li key={f.id}>{f.id}: {f.reason} (decided by {decidedBy(f.decided_by)})</li>
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
