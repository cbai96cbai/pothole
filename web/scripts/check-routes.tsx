// Renders every route to HTML from the data files in the directory given as the first argument
// (the output of `pothole.sitedata.build`), and checks the pages' wording and links.
// Usage: tsx scripts/check-routes.tsx <data dir>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";

import { Pages } from "../src/Pages";
import { match, type Route } from "../src/routes";
import type { Finding, SiteData } from "../src/types";

const dir = process.argv[2];
if (!dir) throw new Error("usage: check-routes.tsx <data dir>");
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

const problems: string[] = [];
const expect = (ok: boolean, what: string) => {
  if (!ok) problems.push(what);
};

const ids = data.findingsIndex.days.flatMap((d) => d.findings.map((f) => f.id));
const hashes = [
  "#/", "#/incidents", "#/drills", "#/findings", "#/repairs", "#/about",
  ...ids.map((id) => `#/findings/${id}`),
  "#/findings/does-not-exist", "#/nowhere", "#/findings/a/b",
];
const lastRun = data.meta.last_success_finished?.slice(0, 10);
const pages = new Map<string, string>();

for (const hash of hashes) {
  const route: Route = match(hash);
  let finding: Finding | null | undefined;
  if (route.page === "finding") {
    finding = ids.includes(route.id) ? read<Finding>(`findings/${route.id}.json`) : null;
  }
  const html = renderToStaticMarkup(<Pages route={route} data={data} finding={finding} />);
  pages.set(hash, html);
  const text = html.replace(/<[^>]+>/g, " ");
  expect(lastRun !== undefined && text.includes(`Last successful run: ${lastRun}`), `${hash}: no last successful run date`);
  expect(!/caused by/i.test(text), `${hash}: says "caused by"`);
  expect(!/false discover/i.test(text), `${hash}: makes a false-discovery claim`);
  expect(html.includes("<h1>"), `${hash}: no heading`);
}

const notFound = ["#/nowhere", "#/findings/does-not-exist", "#/findings/a/b"];
for (const h of notFound) expect(pages.get(h)?.includes("Page not found") === true, `${h}: not a 404 page`);
for (const h of hashes.filter((h) => !notFound.includes(h))) {
  expect(!pages.get(h)?.includes("Page not found"), `${h}: unexpectedly a 404 page`);
}

const text = (h: string) => (pages.get(h) ?? "").replace(/<[^>]+>/g, " ");
if (data.incidents.incidents.some((i) => i.repair.not_judged)) {
  expect(text("#/incidents").includes("Proposed, not judged"), "incidents: proposal not marked as not judged");
}
expect(!(pages.get("#/incidents") ?? "").includes("badge drill"), "incidents: shows a drill");
if (data.drills.drills.length > 0) {
  const n = (pages.get("#/drills") ?? "").split('class="badge drill"').length - 1;
  expect(n >= data.drills.drills.length + 1, "drills: an entry is not labeled Drill");
}
expect(text("#/findings").includes("Nothing notable today") === data.findingsIndex.days.some((d) => d.findings.length === 0), "findings: quiet days not shown");
expect(text("#/repairs").includes("Rule-based fixer"), "repairs: no baseline");
if (!data.repairs.agent_run) expect(text("#/repairs").includes("not yet run"), "repairs: no 'not yet run' for the agent");
expect(text("#/").includes("$4 a month"), "board: monthly cost limit not stated");
expect(text("#/about").includes("$4 a month"), "about: monthly cost limit not stated");
expect(text("#/").includes("Simulated quiet days") && text("#/").includes("Backtest"), "board: quiet-day rate or backtest missing");
for (const id of ids) {
  const h = `#/findings/${id}`;
  expect((pages.get(h) ?? "").includes('aria-label="Daily complaint counts'), `${h}: no series chart`);
  expect((pages.get(h) ?? "").includes("Map of"), `${h}: no map`);
}

// link crawl: every internal link goes to a real page, every other link is https
for (const [hash, html] of pages) {
  for (const m of html.matchAll(/href="([^"]*)"/g)) {
    const href = (m[1] ?? "").replaceAll("&amp;", "&");
    if (href.startsWith("#")) {
      const r = match(href);
      const ok = r.page !== "notfound" && (r.page !== "finding" || ids.includes(r.id));
      expect(ok, `${hash}: broken link ${href}`);
    } else {
      expect(href.startsWith("https://"), `${hash}: link is neither internal nor https: ${href}`);
    }
  }
}

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`checked ${pages.size} routes`);
