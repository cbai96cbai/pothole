// The only data the app reads: the JSON files `pothole.sitedata` writes to ./data.
import backtest from "./data/backtest.json";
import board from "./data/board.json";
import delay from "./data/delay.json";
import drills from "./data/drills.json";
import findingsIndex from "./data/findings/index.json";
import incidents from "./data/incidents.json";
import meta from "./data/meta.json";
import nullSimulation from "./data/null_simulation.json";
import repairs from "./data/repairs.json";
import runs from "./data/runs.json";
import type { Finding, SiteData } from "./types";

export const site = {
  board,
  incidents,
  drills,
  findingsIndex,
  repairs,
  backtest,
  delay,
  nullSimulation,
  runs,
  meta,
} as unknown as SiteData;

const files = import.meta.glob(["./data/findings/*.json", "!./data/findings/index.json"], {
  import: "default",
});

export async function loadFinding(id: string): Promise<Finding | null> {
  const load = files[`./data/findings/${id}.json`];
  return load ? ((await load()) as Finding) : null;
}
