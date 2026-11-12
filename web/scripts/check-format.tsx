// Checks the site's text formatters against fixed cases.
// Usage: tsx scripts/check-format.tsx
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { count, dateTime, oneDecimal, titleCase } from "../src/format";

const problems: string[] = [];
const same = (got: string, want: string, what: string) => {
  if (got !== want) problems.push(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
};

// the table the Python side is tested against too
const cases: [number, string][] = JSON.parse(
  readFileSync(join(import.meta.dirname, "one-decimal-cases.json"), "utf8"),
);
for (const [value, shown] of cases) same(oneDecimal(value), shown, `oneDecimal(${value})`);

// a time states its real zone: New York winter (-05:00) and summer (-04:00) both become UTC
same(dateTime("2026-11-11T15:56:17-05:00"), "2026-11-11 20:56 UTC", "winter offset");
same(dateTime("2026-07-01T23:30:00-04:00"), "2026-07-02 03:30 UTC", "summer offset, next UTC day");
same(dateTime("2026-11-11T20:56:17Z"), "2026-11-11 20:56 UTC", "already UTC");
same(dateTime("2026-11-11T07:05:00+00:00"), "2026-11-11 07:05 UTC", "zero offset");
same(dateTime("2026-11-11T15:56:17"), "2026-11-11 15:56 (zone not recorded)", "no offset");

same(titleCase("HEAT/HOT WATER"), "Heat/Hot Water", "slash");
same(titleCase("NOISE - STREET/SIDEWALK"), "Noise - Street/Sidewalk", "dash");
same(titleCase("Traffic Signal Condition"), "Traffic Signal Condition", "already title case");
same(titleCase("UNSANITARY CONDITION"), "Unsanitary Condition", "plain");

same(count(1, "case"), "1 case", "one");
same(count(0, "case"), "0 cases", "zero");
same(count(2, "case"), "2 cases", "two");

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("checked formatters");
