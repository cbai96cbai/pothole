export function pct(rate: number | null, digits = 0): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(digits)}%`;
}

export function date(iso: string): string {
  return iso.slice(0, 10);
}

const HAS_OFFSET = /(Z|[+-]\d{2}:\d{2})$/;

/** The one way a moment is shown: converted from its own offset to UTC and labelled UTC. A string
 * with no offset is shown as written and says its zone was not recorded. Calendar dates (a
 * finding's day, a chart's days) are never passed through here: they stay dates. */
export function dateTime(iso: string): string {
  const moment = new Date(iso);
  if (!HAS_OFFSET.test(iso) || Number.isNaN(moment.getTime())) {
    return `${iso.slice(0, 10)} ${iso.slice(11, 16)} (zone not recorded)`;
  }
  const utc = moment.toISOString();
  return `${utc.slice(0, 10)} ${utc.slice(11, 16)} UTC`;
}

/** One decimal, a half rounded away from zero: the same rule as `one_decimal` in
 * src/pothole/analyst/numbers.py, tested against the same cases. */
export function oneDecimal(x: number): string {
  return (Math.sign(x) * Math.floor(Math.abs(x) * 10 + 0.5) / 10 + 0).toFixed(1);
}

/** A complaint type from the city's data (upper case) in title case. */
export function titleCase(text: string): string {
  return text.toLowerCase().replace(/(^|[^a-z0-9'])([a-z])/g, (_, before: string, letter: string) =>
    before + letter.toUpperCase(),
  );
}

/** "1 case", "2 cases". */
export function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function usd(amount: string): string {
  return `$${Number(amount).toFixed(3)}`;
}

export function num(n: number): string {
  return n.toLocaleString("en-US");
}

export function small(n: number): string {
  return n < 0.001 ? n.toExponential(1) : n.toPrecision(2);
}

/** A dollar amount from a data file, with at least the cents. */
export function money(amount: string): string {
  const [whole = "0", frac = ""] = Number(Number(amount).toFixed(3)).toString().split(".");
  return `$${whole}.${frac.padEnd(2, "0")}`;
}

/** A tool call as `tool(key=value, ...)`, so two calls of one tool can be told apart. */
export function callText(tool: string, args: Record<string, unknown>): string {
  return `${tool}(${Object.entries(args).map(([k, v]) => `${k}=${String(v)}`).join(", ")})`;
}
