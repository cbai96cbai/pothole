export const REPO_URL = "https://github.com/cbai96cbai/pothole";
export const PROPOSED_NOT_JUDGED = "Proposed, not judged";
export const DRILL = "Drill";
export const NOTHING_NOTABLE = "Nothing notable today";
export const NOT_YET_RUN = "not yet run";
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
