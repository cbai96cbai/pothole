export function pct(rate: number | null, digits = 0): string {
  return rate === null ? "n/a" : `${(rate * 100).toFixed(digits)}%`;
}

export function date(iso: string): string {
  return iso.slice(0, 10);
}

export function dateTime(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
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
