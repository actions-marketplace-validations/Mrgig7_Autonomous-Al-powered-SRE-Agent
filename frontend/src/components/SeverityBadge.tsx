export function SeverityBadge({ value }: { value: string | null | undefined }) {
  const v = (value || "unknown").toLowerCase();
  const cls =
    v === "safe" || v === "ok" || v === "passed" || v === "pass"
      ? "severity-badge success"
      : v === "warn" ||
          v === "warning" ||
          v === "needs-review" ||
          v === "unknown"
        ? "severity-badge warning"
        : "severity-badge error";

  return <span className={cls}>{value || "unknown"}</span>;
}
