import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../api/client';
import { DiffViewer } from '../components/DiffViewer';
import { JsonViewer } from '../components/JsonViewer';
import { SeverityBadge } from '../components/SeverityBadge';
import { Timeline, TimelineStep } from '../components/Timeline';

type EvidenceLine = {
  idx: number;
  line: string;
  tag: string;
  operation_idx?: number | null;
};

type ConfidenceFactor = {
  factor: string;
  value: number;
  weight: number;
  note: string;
};

type FailureExplain = {
  failure_id: string;
  repo: string;
  summary: {
    category?: string | null;
    root_cause?: string | null;
    adapter?: string | null;
    confidence: number;
    confidence_breakdown: ConfidenceFactor[];
  };
  evidence: EvidenceLine[];
  proposed_fix: {
    plan?: any;
    files: string[];
    diff_available: boolean;
  };
  safety: any;
  validation: any;
  run: {
    run_id?: string | null;
    status?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
  };
  timeline: TimelineStep[];
  generated_at: string;
};

type RunDiff = { diff_text: string; stats?: any };

const tabs = ['Overview', 'Evidence', 'Proposed Fix', 'Safety & Scans', 'Validation', 'Artifact'] as const;
type Tab = (typeof tabs)[number];

export default function FailureDetails() {
  const { failureId } = useParams();
  const [tab, setTab] = useState<Tab>('Overview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [explain, setExplain] = useState<FailureExplain | null>(null);
  const [diff, setDiff] = useState<RunDiff | null>(null);
  const [artifact, setArtifact] = useState<any>(null);
  const [timeline, setTimeline] = useState<TimelineStep[]>([]);

  const runId = explain?.run?.run_id || null;

  useEffect(() => {
    if (!failureId) return;
    let cancelled = false;
    const id = failureId;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const explainData = await api.getFailureExplain(id);
        if (cancelled) return;
        setExplain(explainData);
        setTimeline(explainData.timeline || []);

        if (explainData.run?.run_id) {
          const [artifactData, diffData, timelineData] = await Promise.all([
            api.getRunArtifact(explainData.run.run_id).catch(() => null),
            api.getRunDiff(explainData.run.run_id).catch(() => null),
            api.getRunTimeline(explainData.run.run_id).catch(() => null),
          ]);
          if (cancelled) return;
          if (artifactData) setArtifact(artifactData);
          if (diffData) setDiff(diffData);
          if (timelineData?.timeline) setTimeline(timelineData.timeline);
        }
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message || 'Failed to load failure details');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [failureId]);

  const confidencePct = useMemo(() => {
    const v = explain?.summary?.confidence ?? 0;
    return `${Math.round(v * 100)}%`;
  }, [explain]);

  function downloadArtifact() {
    const data = artifact || explain;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `failure_${failureId}_artifact.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return (
      <div className="page">
        <div className="loading-spinner"></div>
      </div>
    );
  }

  if (error || !explain) {
    return (
      <div className="page">
        <div className="error-message">{error || 'Not found'}</div>
        <Link to="/app" className="link">Back to dashboard</Link>
      </div>
    );
  }

  return (
    <div className="page failure-details">
      <div className="page-header">
        <div>
          <h2>Failure Detail</h2>
          <div className="muted">
            <span>{explain.repo}</span>
            <span className="dot-sep">•</span>
            <span>{explain.failure_id}</span>
            {runId && (
              <>
                <span className="dot-sep">•</span>
                <span>run {runId}</span>
              </>
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to="/app/events" className="link">Back</Link>
          <button className="btn" onClick={downloadArtifact}>Download JSON</button>
        </div>
      </div>

      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t}
            className={`tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Overview' && (
        <div className="card">
          <div className="kv-grid">
            <div>
              <div className="muted">Category</div>
              <div>{explain.summary.category || 'unknown'}</div>
            </div>
            <div>
              <div className="muted">Adapter</div>
              <div>{explain.summary.adapter || 'unknown'}</div>
            </div>
            <div>
              <div className="muted">Confidence</div>
              <div>{confidencePct}</div>
            </div>
            <div>
              <div className="muted">Policy Label</div>
              <SeverityBadge value={explain.safety?.label} />
            </div>
          </div>

          <div className="section">
            <div className="section-title">Root Cause</div>
            <div className="preline">{explain.summary.root_cause || 'N/A'}</div>
          </div>

          <div className="section">
            <div className="section-title">Confidence Reasoning</div>
            {explain.summary.confidence_breakdown.length ? (
              <ul className="list">
                {explain.summary.confidence_breakdown.map((f, i) => (
                  <li key={i}>
                    <span className="mono">{f.factor}</span> {Math.round(f.value * 100)}% (w={f.weight}) — {f.note}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="muted">No confidence breakdown available.</div>
            )}
          </div>

          <div className="section">
            <div className="section-title">Pipeline Timeline</div>
            <Timeline steps={timeline || []} />
          </div>
        </div>
      )}

      {tab === 'Evidence' && (
        <div className="card">
          {explain.evidence.length ? (
            <div className="evidence">
              {explain.evidence.map((e, i) => (
                <div key={i} className="evidence-row">
                  <span className="evidence-idx">{e.idx}</span>
                  <span className="evidence-tag">{e.tag}</span>
                  <span className="evidence-line">{e.line}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted">No evidence extracted.</div>
          )}
        </div>
      )}

      {tab === 'Proposed Fix' && (
        <div className="card">
          <div className="section">
            <div className="section-title">Plan</div>
            <JsonViewer value={explain.proposed_fix.plan} />
          </div>
          <div className="section">
            <div className="section-title">Diff</div>
            <DiffViewer diffText={diff?.diff_text || ''} />
          </div>
        </div>
      )}

      {tab === 'Safety & Scans' && (
        <div className="card">
          <div className="kv-grid">
            <div>
              <div className="muted">Danger Score</div>
              <div className="mono">{explain.safety?.danger_score ?? 'N/A'}</div>
            </div>
            <div>
              <div className="muted">Label</div>
              <SeverityBadge value={explain.safety?.label} />
            </div>
          </div>

          <div className="section">
            <div className="section-title">Danger Breakdown</div>
            {explain.safety?.danger_breakdown?.length ? (
              <ul className="list">
                {explain.safety.danger_breakdown.map((r: any, i: number) => (
                  <li key={i}>
                    <span className="mono">{r.code}</span> {r.weight}: {r.message}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="muted">No danger breakdown available.</div>
            )}
          </div>

          <div className="section">
            <div className="section-title">Policy Violations</div>
            {explain.safety?.violations?.length ? (
              <ul className="list">
                {explain.safety.violations.map((v: any, i: number) => (
                  <li key={i}>
                    <span className="mono">{v.code}</span> ({v.severity}) {v.message}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="muted">No violations.</div>
            )}
          </div>

          <div className="section">
            <div className="section-title">Scans</div>
            <JsonViewer value={explain.validation?.scans} />
          </div>
        </div>
      )}

      {tab === 'Validation' && (
        <div className="card">
          <div className="kv-grid">
            <div>
              <div className="muted">Sandbox</div>
              <SeverityBadge value={explain.validation?.sandbox} />
            </div>
            <div>
              <div className="muted">Tests</div>
              <SeverityBadge value={explain.validation?.tests} />
            </div>
            <div>
              <div className="muted">Lint</div>
              <SeverityBadge value={explain.validation?.lint} />
            </div>
          </div>
          <div className="section">
            <div className="section-title">Raw Validation JSON</div>
            <JsonViewer value={artifact?.validation || null} />
          </div>
        </div>
      )}

      {tab === 'Artifact' && (
        <div className="card">
          <JsonViewer value={artifact || null} />
        </div>
      )}
    </div>
  );
}

