function isPlainObject(value: any): value is Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value);
}

function NodeView({
  name,
  value,
  level,
}: {
  name: string;
  value: any;
  level: number;
}) {
  if (value == null) {
    return (
      <div className="json-row">
        <span className="json-key">{name}:</span> <span className="json-null">null</span>
      </div>
    );
  }

  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return (
      <div className="json-row">
        <span className="json-key">{name}:</span>{' '}
        <span className="json-value">{String(value)}</span>
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <details className="json-node" open={level < 1}>
        <summary className="json-summary">
          {name} <span className="muted">[{value.length}]</span>
        </summary>
        <div className="json-children">
          {value.map((v, i) => (
            <NodeView key={i} name={String(i)} value={v} level={level + 1} />
          ))}
        </div>
      </details>
    );
  }

  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    return (
      <details className="json-node" open={level < 1}>
        <summary className="json-summary">
          {name} <span className="muted">{'{'}{keys.length}{'}'}</span>
        </summary>
        <div className="json-children">
          {keys.map((k) => (
            <NodeView key={k} name={k} value={value[k]} level={level + 1} />
          ))}
        </div>
      </details>
    );
  }

  return (
    <div className="json-row">
      <span className="json-key">{name}:</span> <span className="json-value">{String(value)}</span>
    </div>
  );
}

export function JsonViewer({ value }: { value: any }) {
  if (value == null) return <div className="muted">No data.</div>;
  return (
    <div className="json-viewer">
      <NodeView name="root" value={value} level={0} />
    </div>
  );
}

