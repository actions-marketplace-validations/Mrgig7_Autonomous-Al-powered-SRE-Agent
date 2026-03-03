"""Send a signed test webhook to reproduce the session error."""

import hashlib
import hmac
import json
import urllib.error
import urllib.request

body = json.dumps(
    {
        "action": "completed",
        "workflow_job": {
            "id": 99999,
            "run_id": 88888,
            "run_attempt": 1,
            "name": "test-job",
            "workflow_name": "CI",
            "conclusion": "failure",
            "status": "completed",
            "head_sha": "abc123def456789012345678901234567890",
            "head_branch": "main",
            "created_at": "2025-01-01T00:00:00Z",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:01:00Z",
            "steps": [
                {
                    "name": "Run tests",
                    "status": "completed",
                    "conclusion": "failure",
                    "number": 1,
                }
            ],
        },
        "repository": {
            "id": 1,
            "name": "test",
            "full_name": "test/test-repo",
            "html_url": "https://github.com/test/test-repo",
            "owner": {"login": "test", "id": 1},
        },
        "sender": {"login": "test", "id": 1},
    }
).encode()

secret = b"sre-harness-webhook-secret-2026"
sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

req = urllib.request.Request(
    "http://localhost:8000/webhooks/github",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "workflow_job",
        "X-GitHub-Delivery": "test-debug-session-002",
        "X-Hub-Signature-256": sig,
    },
    method="POST",
)

try:
    resp = urllib.request.urlopen(req)
    print(f"Status: {resp.status}")
    print(f"Body: {resp.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"Error: {e.code}")
    print(f"Body: {e.read().decode()}")
