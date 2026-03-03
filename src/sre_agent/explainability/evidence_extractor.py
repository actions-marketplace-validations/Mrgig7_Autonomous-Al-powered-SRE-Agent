from __future__ import annotations

import re
from dataclasses import dataclass

from sre_agent.explainability.redactor import get_redactor


@dataclass(frozen=True)
class EvidenceLine:
    idx: int
    line: str
    tag: str
    operation_idx: int | None = None


_TAG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("root-cause", re.compile(r"ModuleNotFoundError: No module named", re.IGNORECASE)),
    ("root-cause", re.compile(r"No module named ['\"][^'\"]+['\"]", re.IGNORECASE)),
    ("root-cause", re.compile(r"Cannot find module ['\"][^'\"]+['\"]", re.IGNORECASE)),
    ("root-cause", re.compile(r"missing go\.sum entry", re.IGNORECASE)),
    ("root-cause", re.compile(r"no required module provides package", re.IGNORECASE)),
    ("root-cause", re.compile(r"dependencies\.dependency\.version.*is missing", re.IGNORECASE)),
    ("root-cause", re.compile(r"failed to solve:", re.IGNORECASE)),
    ("test-failure", re.compile(r"^FAILED\b", re.IGNORECASE)),
    ("test-failure", re.compile(r"\bFAIL\b", re.IGNORECASE)),
    ("npm", re.compile(r"\bnpm ERR!\b", re.IGNORECASE)),
    ("go", re.compile(r"^\s*go:\s", re.IGNORECASE)),
    ("maven", re.compile(r"^\[ERROR\]", re.IGNORECASE)),
    ("docker", re.compile(r"\bdocker build\b", re.IGNORECASE)),
]


def extract_evidence_lines(log_text: str, *, max_lines: int = 30) -> list[EvidenceLine]:
    redactor = get_redactor()
    lines = log_text.splitlines()
    candidates: list[EvidenceLine] = []

    for i, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        for tag, pat in _TAG_PATTERNS:
            if pat.search(raw):
                candidates.append(EvidenceLine(idx=i, line=redactor.redact_text(raw), tag=tag))
                break

    for i, raw in enumerate(lines, start=1):
        if "Traceback (most recent call last)" in raw:
            for j in range(i, min(len(lines) + 1, i + 20)):
                line_text = lines[j - 1]
                if line_text.strip():
                    candidates.append(
                        EvidenceLine(
                            idx=j,
                            line=redactor.redact_text(line_text),
                            tag="stack-trace",
                        )
                    )
            break

    seen: set[int] = set()
    ranked: list[EvidenceLine] = []
    tag_priority = {
        "root-cause": 0,
        "stack-trace": 1,
        "test-failure": 2,
        "maven": 3,
        "go": 3,
        "npm": 3,
        "docker": 3,
    }
    candidates.sort(key=lambda e: (tag_priority.get(e.tag, 10), e.idx))
    for e in candidates:
        if e.idx in seen:
            continue
        ranked.append(e)
        seen.add(e.idx)
        if len(ranked) >= max_lines:
            break
    return ranked


def attach_operation_links(
    evidence: list[EvidenceLine], *, operations: list[dict] | None
) -> list[EvidenceLine]:
    if not operations:
        return evidence
    by_token: list[tuple[int, list[str]]] = []
    for op_idx, op in enumerate(operations):
        tokens: list[str] = []
        for line in op.get("evidence") or []:
            token = str(line).strip()
            if token:
                tokens.append(token)
        if tokens:
            by_token.append((op_idx, tokens))

    out: list[EvidenceLine] = []
    for e in evidence:
        linked: int | None = None
        for op_idx, tokens in by_token:
            if any(t in e.line for t in tokens):
                linked = op_idx
                break
        out.append(EvidenceLine(idx=e.idx, line=e.line, tag=e.tag, operation_idx=linked))
    return out
