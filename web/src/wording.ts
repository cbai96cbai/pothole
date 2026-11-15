export const REPO_URL = "https://github.com/cbai96cbai/pothole";
export const PROPOSED_NOT_JUDGED = "Proposed, not judged";
export const DRILL = "Drill";
export const NOTHING_NOTABLE = "Nothing notable that day";
// a day the screen flagged but nobody investigated is not a quiet day
export const FLAGGED_NOT_INVESTIGATED = "Flagged by the screen, not investigated";
export const NOT_YET_RUN = "not yet run";
export const WEATHER_SOURCE_WORDS: Record<string, string> = {
  archive: "recorded weather",
  forecast_past_days: "forecast model",
};
export const EVENTS_NOTE =
  "Events shown are permitted events that list this district and whose scheduled span covers the day. A long span does not mean activity throughout it.";
export const EVENTS_EMPTY = "No permitted event listing this district covered the day.";
export const EVENTS_UNAVAILABLE =
  "The events lookup was unavailable, so it is not known whether any events were listed.";
export const EVENTS_CUT_SHORT = "The events list was cut short at its row limit and may be incomplete.";
export const WEATHER_UNAVAILABLE = "The weather lookup was unavailable.";
export const UNFINISHED = "The investigation did not finish; only the counts are shown.";

export const SCREEN_NOTE =
  "The detector is a screen: it picks out unusual counts for a person to look at. A flag is not proof of an event, and a finding says what was associated with a spike, not what caused it.";

export const STATUS_WORDS: Record<string, string> = {
  success: "Healthy: the last run finished and passed its checks",
  checks_failed: "A check failed on the last run",
  source_unavailable: "The city's data service was unavailable on the last run",
  incomplete_load: "The last run loaded only part of the data",
  error: "The last run stopped with an error",
  no_runs: "No runs recorded yet",
};

export const ACTION_WORDS: Record<string, string> = {
  patch: "Proposed a fix to the pipeline definition",
  issue: "Opened an issue for a person to handle",
  no_change: "Decided nothing needed changing",
  none: "No repair step",
};

export const DECIDED_BY: Record<string, string> = {
  rule: "the volume rule",
  code: "the code (the model could not decide)",
  agent: "the agent",
  baseline: "the rule-based fixer",
  unknown: "not recorded",
};
export const decidedBy = (by: string) => DECIDED_BY[by] ?? by;
