import type { ReactNode } from "react";

/** A table that scrolls sideways on a narrow screen: a visible cue, reachable by keyboard, named. */
export function Scroll({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <p className="muted scroll-hint">Scroll sideways to see every column.</p>
      <div className="scroll" tabIndex={0} role="region" aria-label={label}>
        {children}
      </div>
    </>
  );
}
