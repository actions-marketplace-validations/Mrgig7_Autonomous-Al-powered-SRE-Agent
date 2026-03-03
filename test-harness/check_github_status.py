"""Check GitHub for harness repo and workflow status."""

import os

import httpx

token = os.environ.get("GITHUB_TOKEN", "")
client = httpx.Client(
    base_url="https://api.github.com",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    },
    timeout=15.0,
)

try:
    resp = client.get("/user/repos", params={"sort": "created", "per_page": 5})
    resp.raise_for_status()
    repos = resp.json()
    for r in repos:
        name = r["name"]
        full = r["full_name"]
        if "sre-harness" in name:
            print(f"Found: {full} (created: {r['created_at']})")
            runs_resp = client.get(
                f"/repos/{full}/actions/runs",
                params={"per_page": 5},
            )
            runs = runs_resp.json().get("workflow_runs", [])
            for run in runs:
                rname = run["name"]
                status = run["status"]
                conclusion = run.get("conclusion")
                print(f"  Run: {rname} status={status} conclusion={conclusion}")
            break
    else:
        print("No sre-harness repo found in recent repos")
except Exception as e:
    print(f"Error: {e}")
finally:
    client.close()
