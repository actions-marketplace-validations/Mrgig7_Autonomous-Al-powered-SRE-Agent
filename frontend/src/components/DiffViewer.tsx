import { useMemo } from 'react';

type DiffSection = {
  title: string;
  body: string;
};

function splitDiff(diffText: string): DiffSection[] {
  const lines = diffText.split('\n');
  const sections: DiffSection[] = [];
  let currentTitle = 'diff';
  let current: string[] = [];

  const flush = () => {
    if (!current.length) return;
    sections.push({ title: currentTitle, body: current.join('\n') });
    current = [];
  };

  for (const line of lines) {
    if (line.startsWith('diff --git ')) {
      flush();
      currentTitle = line.replace('diff --git ', '');
    }
    current.push(line);
  }
  flush();
  return sections;
}

export function DiffViewer({ diffText }: { diffText: string }) {
  const sections = useMemo(() => splitDiff(diffText), [diffText]);

  if (!diffText) return <div className="muted">No diff available.</div>;

  if (sections.length <= 1) {
    return (
      <pre className="diff-pre">
        {diffText}
      </pre>
    );
  }

  return (
    <div className="diff-viewer">
      {sections.map((s, i) => (
        <details key={i} open={i === 0} className="diff-section">
          <summary className="diff-summary">{s.title}</summary>
          <pre className="diff-pre">{s.body}</pre>
        </details>
      ))}
    </div>
  );
}

