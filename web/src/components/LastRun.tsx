import { date } from "../format";
import type { Meta } from "../types";

export function LastRun({ meta }: { meta: Meta }) {
  return (
    <p className="lastrun" data-testid="last-run">
      {meta.last_success_finished ? (
        <>Last successful run: {date(meta.last_success_finished)}</>
      ) : (
        <>No successful run yet</>
      )}
      <span className="muted"> · site built {date(meta.generated)}</span>
    </p>
  );
}
