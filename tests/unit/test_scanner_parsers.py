from __future__ import annotations

import json

from sre_agent.sandbox.scanners.gitleaks import parse_gitleaks_report
from sre_agent.sandbox.scanners.trivy import parse_trivy_report


def test_parse_gitleaks_report_redacts_secret_fields() -> None:
    report = [
        {
            "RuleID": "generic-api-key",
            "File": "src/app.py",
            "Secret": "supersecret",
            "Match": 'api_key = "supersecret"',
        }
    ]
    findings = parse_gitleaks_report(json.dumps(report))
    assert len(findings) == 1
    assert findings[0].rule_id == "generic-api-key"
    assert findings[0].file_path_hash
    assert "supersecret" not in findings[0].model_dump_json()


def test_parse_trivy_report_counts_severity_and_packages() -> None:
    report = {
        "Results": [
            {
                "Target": "requirements.txt",
                "Vulnerabilities": [
                    {"PkgName": "requests", "Severity": "HIGH"},
                    {"PkgName": "requests", "Severity": "HIGH"},
                    {"PkgName": "urllib3", "Severity": "CRITICAL"},
                ],
            }
        ]
    }
    severity_counts, top = parse_trivy_report(json.dumps(report))
    assert severity_counts["HIGH"] == 2
    assert severity_counts["CRITICAL"] == 1
    top_names = {p.name: p.count for p in top}
    assert top_names["requests"] == 2
    assert top_names["urllib3"] == 1
