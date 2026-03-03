from __future__ import annotations

import json
from typing import Any

import httpx


class APIClientError(RuntimeError):
    pass


class SREApiClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds)
        self._access_token: str | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self, email: str, password: str) -> str:
        response = await self._client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise APIClientError("Login succeeded but no access token was returned")
        self._access_token = token
        return token

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise APIClientError("Not authenticated")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def get_dashboard_events(
        self,
        *,
        repository: str,
        branch: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        response = await self._client.get(
            "/api/v1/dashboard/events",
            params={"repository": repository, "branch": branch, "limit": limit},
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_analysis(self, failure_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/failures/{failure_id}/analysis",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_run_artifact(self, run_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/runs/{run_id}/artifact",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_run_diff(self, run_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/runs/{run_id}/diff",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_run_timeline(self, run_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/runs/{run_id}/timeline",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def get_metrics(self) -> str:
        response = await self._client.get("/metrics")
        response.raise_for_status()
        return response.text

    async def wait_for_dashboard_event(
        self,
        *,
        failure_id: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        headers = self._auth_headers()
        headers["Accept"] = "text/event-stream"
        async with self._client.stream(
            "GET",
            "/api/v1/dashboard/stream",
            headers=headers,
            timeout=timeout_seconds,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if str(payload.get("failure_id")) == str(failure_id):
                    return payload
        raise APIClientError(f"Timed out waiting for SSE event for failure_id={failure_id}")


class GitHubApiClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def list_pulls_by_head(
        self,
        repository: str,
        owner: str,
        branch: str,
        *,
        state: str = "open",
    ) -> list[dict]:
        response = await self._client.get(
            f"/repos/{repository}/pulls",
            params={"state": state, "head": f"{owner}:{branch}"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def get_pull(self, repository: str, number: int) -> dict[str, Any]:
        response = await self._client.get(f"/repos/{repository}/pulls/{number}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
