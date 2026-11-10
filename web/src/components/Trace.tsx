const LIMIT = 1500;

export function Trace({ trace }: { trace: Record<string, unknown>[] }) {
  if (trace.length === 0) return <p className="muted">No trace was recorded for this finding.</p>;
  return (
    <div className="trace">
      {trace.map((step, i) => {
        const text = JSON.stringify(step, null, 2);
        return (
          <details key={i}>
            <summary>Step {i + 1}</summary>
            <pre>{text.length > LIMIT ? `${text.slice(0, LIMIT)}\n... (shortened)` : text}</pre>
          </details>
        );
      })}
    </div>
  );
}
