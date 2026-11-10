export type Route =
  | { page: "board" }
  | { page: "incidents" }
  | { page: "drills" }
  | { page: "findings" }
  | { page: "finding"; id: string }
  | { page: "repairs" }
  | { page: "about" }
  | { page: "notfound"; path: string };

export const NAV: { label: string; href: string; page: Route["page"] }[] = [
  { label: "Board", href: "#/", page: "board" },
  { label: "Incidents", href: "#/incidents", page: "incidents" },
  { label: "Drills", href: "#/drills", page: "drills" },
  { label: "Findings", href: "#/findings", page: "findings" },
  { label: "Repairs", href: "#/repairs", page: "repairs" },
  { label: "About", href: "#/about", page: "about" },
];

export function findingHref(id: string): string {
  return `#/findings/${encodeURIComponent(id)}`;
}

export function match(hash: string): Route {
  const path = hash.replace(/^#/, "").replace(/\/+$/, "") || "/";
  if (path === "/") return { page: "board" };
  const parts = path.split("/").filter(Boolean);
  const [head, rest, extra] = parts;
  if (extra === undefined && rest === undefined) {
    if (head === "incidents") return { page: "incidents" };
    if (head === "drills") return { page: "drills" };
    if (head === "findings") return { page: "findings" };
    if (head === "repairs") return { page: "repairs" };
    if (head === "about") return { page: "about" };
  }
  if (head === "findings" && rest !== undefined && extra === undefined) {
    return { page: "finding", id: decodeURIComponent(rest) };
  }
  return { page: "notfound", path };
}
