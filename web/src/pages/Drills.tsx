import type { IncidentItem } from "../types";
import { DRILL } from "../wording";
import { IncidentList } from "./Incidents";

export function Drills({ items }: { items: IncidentItem[] }) {
  return (
    <>
      <h1>{DRILL}s</h1>
      <p>
        <span className="badge drill">{DRILL}</span> A drill is a practice run: a known fault is
        planted in a copy of the data to check that the repair step notices and handles it. Drills
        are not real failures, and they run on Sundays when nothing real needed repair. The
        scored drill results are on the <a href="#/repairs">Repairs</a> page.
      </p>
      <IncidentList items={items} empty="No drill has run yet." />
    </>
  );
}
