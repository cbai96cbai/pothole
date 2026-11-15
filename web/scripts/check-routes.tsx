// Renders every route to HTML from the data files in the directory given as the first argument
// (the output of `pothole.sitedata.build`), and checks the pages' wording and links.
// Usage: tsx scripts/check-routes.tsx <data dir>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";

import { callText, dateTime, oneDecimal, titleCase } from "../src/format";
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
const lastRun = data.meta.last_success_finished ? dateTime(data.meta.last_success_finished) : undefined;
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

for (const [hash, html] of pages) {
  for (const m of html.matchAll(/<div class="scroll"([^>]*)>/g)) {
    const attrs = m[1] ?? "";
    expect(attrs.includes('tabindex="0"') && /aria-label="[^"]+"/.test(attrs), `${hash}: a scrolling table is not reachable by keyboard or has no label`);
  }
  const boxes = html.split('class="scroll"').length - 1;
  const hints = html.split("scroll-hint").length - 1;
  expect(boxes === hints, `${hash}: a scrolling table has no visible cue`);
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
expect(!text("#/findings").includes("leftover summary"), "findings: an unfinished finding shows its summary");
const anyUnfinished = data.findingsIndex.days.some((d) => d.findings.some((f) => f.status === "unfinished"));
expect(!anyUnfinished || text("#/findings").includes("did not finish"), "findings: unfinished finding not marked");
const emptyDays = data.findingsIndex.days.filter((d) => d.findings.length === 0);
expect(text("#/findings").includes("Nothing notable that day") === emptyDays.some((d) => d.cleared === 0), "findings: quiet days not shown, or a flagged day called quiet");
expect(text("#/findings").includes("Flagged by the screen, not investigated") === emptyDays.some((d) => d.cleared > 0), "findings: a flagged day without findings is not said");
expect(text("#/repairs").includes("Rule-based fixer"), "repairs: no baseline");
if (!data.repairs.agent_run) expect(text("#/repairs").includes("not yet run"), "repairs: no 'not yet run' for the agent");
const f = data.meta.facts;
const limit = `$${Number(f.monthly_limit).toFixed(2)} a month`;
expect(text("#/").includes(limit), "board: monthly cost limit not stated from the data");
expect(text("#/about").includes(limit), "about: monthly cost limit not stated from the data");
for (const figure of [f.window_days, f.complaint_types, f.districts, f.max_investigations]) {
  expect(text("#/about").includes(String(figure)), `about: ${figure} from the facts is not shown`);
}
expect(!/repairs (itself|its own)|fixes itself/i.test(text("#/about")), "about: says the pipeline repairs itself");
expect(text("#/about").includes("reviews and merges"), "about: repairs are not described as reviewed pull requests");
const tested = data.backtest.days;
if (tested.length > 0) {
  const span = `(${tested[0]?.day} to ${tested[tested.length - 1]?.day})`;
  expect(text("#/").replace(/\s+/g, " ").includes(span), `board: the past-days span is not the tested days ${span}`);
}
expect(text("#/").includes(`Last run: ${dateTime(data.board.last_run?.finished ?? "")}`) || !data.board.last_run, "board: last run time not in the one formatter's form");
expect(text("#/").includes("On simulated quiet days") && text("#/").includes("On past days"), "board: quiet-day rate or backtest missing");
// as React writes text into HTML
const escapeHtml = (t: string) =>
  t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
const UNFINISHED = "The investigation did not finish; only the counts are shown.";
for (const id of ids) {
  const h = `#/findings/${id}`;
  const html = pages.get(h) ?? "";
  const file = read<Finding>(`findings/${id}.json`);
  expect(html.includes('aria-label="Daily complaint counts'), `${h}: no series chart`);
  expect(html.includes("Map of"), `${h}: no map`);
  const file0 = read<Finding>(`findings/${id}.json`);
  const plain = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  // reader-facing content only: the developer trace legitimately holds raw names
  const reader = (html.split('<details class="dev">')[0] ?? "").replace(/<[^>]+>/g, " ");
  expect(!/forecast_past_days|archive/.test(reader), `${h}: a source's raw name is shown`);
  expect(plain.includes(`${titleCase(file0.complaint_type)}, ${file0.borough} community district`), `${h}: complaint type not in title case`);
  expect(plain.includes(`Expected ${oneDecimal(file0.expected)}`), `${h}: expected value not rounded by the shared rule`);
  expect(plain.includes(`${oneDecimal(file0.ratio)}x`), `${h}: ratio not rounded by the shared rule`);
  expect(plain.includes(`expected ${oneDecimal(file0.expected)}`), `${h}: the chart does not label the expected value`);
  expect(plain.includes("list this district") && plain.includes("not mean activity throughout"), `${h}: events not described`);
  expect(file0.data_starts === null || plain.includes(`Data starts ${file0.data_starts}`), `${h}: the chart does not say where the data starts`);
  expect(file0.events_state !== "unavailable" || plain.includes("events lookup was unavailable"), `${h}: an unavailable events lookup is not said`);
  expect(html.includes("Trace (for developers)"), `${h}: trace not labelled for developers`);
  if (file.status === "unfinished") {
    expect(html.includes(UNFINISHED), `${h}: unfinished finding not marked`);
    expect(!html.includes("What the summary relied on"), `${h}: unfinished finding lists evidence`);
    // the summary block is the plain paragraph the investigated page puts its summary in
    expect(!html.includes(`<p>${escapeHtml(file.summary)}</p>`), `${h}: unfinished finding shows a summary`);
    const shown = file.notes.filter((n) => !n.startsWith("unfinished:"));
    expect(shown.length === 0 || html.includes("Not computed:"), `${h}: notes not shown`);
    expect(!html.includes("Not computed: unfinished"), `${h}: notes repeat "unfinished"`);
  } else {
    expect(!html.includes(UNFINISHED), `${h}: finished finding marked unfinished`);
    expect(html.includes("What the summary relied on") && html.includes(`<p>${escapeHtml(file.summary)}</p>`),`${h}: summary or evidence missing`);
    let at = 0;
    for (const e of file.evidence) {
      expect(html.includes(escapeHtml(callText(e.tool, e.args))), `${h}: evidence does not show the call's arguments`);
      const label = `ref ${e.ref} \u00b7 `;
      const found = html.indexOf(label, at);
      expect(found >= 0, `${h}: evidence item is not labelled "ref ${e.ref}" in ref order`);
      at = Math.max(at, found);
      if (e.tool === "weather") expect(/recorded weather|forecast model/.test(reader), `${h}: weather evidence not in plain words`);
      if (e.tool === "events" && e.excerpt.includes('"shown"')) {
        const shown = /"shown": "([^"]+)"/.exec(e.excerpt)?.[1] ?? "";
        expect(reader.includes(shown), `${h}: a shortened events excerpt hides its "${shown}" notice`);
      }
    }
  }
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
