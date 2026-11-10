// Renders one route to HTML on stdout from the data files in the directory given first.
// Usage: tsx scripts/render-page.tsx <data dir> <hash>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";

import { Pages } from "../src/Pages";
import { match } from "../src/routes";
import type { SiteData } from "../src/types";

const [dir, hash] = process.argv.slice(2);
if (!dir || !hash) throw new Error("usage: render-page.tsx <data dir> <hash>");
const read = <T,>(name: string): T => JSON.parse(readFileSync(join(dir, name), "utf8")) as T;
const data: SiteData = {
  board: read("board.json"),
  incidents: read("incidents.json"),
  drills: read("drills.json"),
  findingsIndex: read("findings/index.json"),
  repairs: read("repairs.json"),
  backtest: read("backtest.json"),
  delay: read("delay.json"),
  nullSimulation: read("null_simulation.json"),
  runs: read("runs.json"),
  meta: read("meta.json"),
};
console.log(renderToStaticMarkup(<Pages route={match(hash)} data={data} />).replace(/<[^>]+>/g, " "));
