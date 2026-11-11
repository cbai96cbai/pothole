// Mirrors web/schema/*.schema.json by hand; the Python tests validate every data file against
// the schemas, so a change there must be made here.

export interface RunSummary {
  run_id: string;
  started: string;
  finished: string;
  status: string;
  rows_loaded: number;
  pages: number;
  analyst_day: string | null;
  ai_cost: string;
  generation: string | null;
}
export type Level = "pass" | "warn" | "fail";
export interface Check {
  name: string;
  level: Level;
  detail: string;
}
export interface LastCheck extends Check {
  run_id: string;
  finished: string;
}
export interface LatestDay {
  day: string;
  tested: number;
  cleared: number;
  findings: number;
  skipped_reason: string | null;
}
export interface Board {
  dataset_status: string;
  last_run: RunSummary | null;
  last_success: RunSummary | null;
  checks: LastCheck[];
  latest_day: LatestDay | null;
  month: string | null;
  month_ai_cost: string;
}
export interface Repair {
  action: string;
  drill: boolean;
  url: string | null;
  verdict: string | null;
  not_judged: boolean;
  decided_by: string;
}
export interface IncidentItem {
  run_id: string;
  started: string;
  status: string;
  repair: Repair;
  failed_checks: Check[];
  ai_cost: string;
}
export interface Incidents {
  incidents: IncidentItem[];
}
export interface Drills {
  drills: IncidentItem[];
}
export interface FindingCard {
  id: string;
  status: "investigated" | "unfinished";
  borough: string;
  board: number;
  complaint_type: string;
  observed: number;
  expected: number;
  ratio: number;
  q: number;
  summary: string;
}
export interface FindingDay {
  day: string;
  tested: number;
  cleared: number;
  skipped_reason: string | null;
  findings: FindingCard[];
}
export interface FindingsIndex {
  days: FindingDay[];
}
export interface Rate {
  correct: number;
  of: number;
  rate: number | null;
}
export interface Share {
  count: number;
  of: number;
  rate: number | null;
}
export interface Detected {
  detected: number;
  of: number;
  rate: number | null;
}
export interface Family {
  expected: string;
  cases: number;
  correct: number;
  detected: number;
  decided_by?: Record<string, number> | null;
}
export interface CaseFailure {
  id: string;
  family: string;
  expected: string;
  detected: boolean;
  action: string;
  correct: boolean;
  reason: string;
  decided_by?: string | null;
}
export interface RepairSummary {
  repairer: string;
  split: string;
  cases: number;
  repair_success: Rate | null;
  issue_accuracy: Rate | null;
  no_change_accuracy: Rate | null;
  repairable_answered_with_issue: Share;
  crashed: number;
  detection_coverage_fault_cases: Detected;
  detection_coverage_benign_control: Detected;
  per_family: Record<string, Family>;
  failures: CaseFailure[];
  decided_by?: Record<string, number> | null;
  repairer_decided?: Record<string, unknown> | null;
  complete?: boolean | null;
}
export interface Repairs {
  summaries: RepairSummary[];
  agent_run: boolean;
}
export interface FlagRow {
  borough: string;
  board: number;
  complaint_type: string;
  observed: number;
  expected: number;
  ratio: number;
  q: number;
}
export interface BacktestDay {
  day: string;
  series_tested: number;
  cleared_the_bar: number;
  top: FlagRow[];
}
export interface Backtest {
  label: string;
  window: string[];
  days_tested: number;
  flags_per_day: { min: number; median: number; max: number };
  days: BacktestDay[];
}
export interface Delay {
  label: string;
  reload_at: string;
  rows_measured_at_lag_0: number;
  rows_measured_note: string;
  rows_by_lag_days: Record<string, number>;
  share_by_lag_days: Record<string, number>;
}
export interface NullSimulation {
  label: string;
  seed: number;
  series: number;
  days_tested: number;
  days_simulated_after_lead_in: number;
  rule: string;
  dispersion: string;
  share_of_days_with_a_candidate: Record<string, number>;
}
export interface Runs {
  runs: RunSummary[];
}
export interface Facts {
  window_days: number;
  analysis_lag_days: number;
  complaint_types: number;
  districts: number;
  max_investigations: number;
  calls_per_run: number;
  max_cost_per_call: string;
  run_envelope: string;
  run_ceiling: string;
  ceiling_days: number;
  monthly_limit: string;
  price_input_per_million: string;
  price_output_per_million: string;
}
export interface Meta {
  facts: Facts;
  generated: string;
  last_success_finished: string | null;
  last_success_run_id: string | null;
}
export interface Evidence {
  tool: string;
  args: Record<string, unknown>;
  excerpt: string;
}
export interface Finding {
  id: string;
  day: string;
  borough: string;
  board: number;
  complaint_type: string;
  observed: number;
  expected: number;
  ratio: number;
  q: number;
  summary: string;
  evidence: Evidence[];
  series: [string, number][];
  points: [number, number][];
  weather: { precip_mm: number | null; tmax_c: number | null; source: string } | null;
  events: { name: string; type: string; start: string; end: string }[];
  status: "investigated" | "unfinished";
  trace: Record<string, unknown>[];
  cost: string;
  notes: string[];
}

export interface SiteData {
  board: Board;
  incidents: Incidents;
  drills: Drills;
  findingsIndex: FindingsIndex;
  repairs: Repairs;
  backtest: Backtest;
  delay: Delay;
  nullSimulation: NullSimulation;
  runs: Runs;
  meta: Meta;
}
