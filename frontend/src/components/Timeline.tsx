export type TimelineStep = {
  step: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
};

function formatDuration(ms: number | null | undefined) {
  if (ms == null) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function Timeline({ steps }: { steps: TimelineStep[] }) {
  if (!steps.length) return <div className="muted">No timeline available.</div>;

  return (
    <div className="timeline">
      {steps.map((s, idx) => (
        <div key={`${s.step}-${idx}`} className="timeline-row">
          <div className="timeline-step">{s.step}</div>
          <div className={`timeline-status ${String(s.status || '').toLowerCase()}`}>
            {s.status}
          </div>
          <div className="timeline-time">
            {s.started_at ? new Date(s.started_at).toLocaleString() : ''}
          </div>
          <div className="timeline-duration">{formatDuration(s.duration_ms)}</div>
        </div>
      ))}
    </div>
  );
}

