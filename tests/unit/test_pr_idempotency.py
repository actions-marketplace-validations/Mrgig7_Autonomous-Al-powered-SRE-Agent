from __future__ import annotations

from uuid import uuid4

import pytest
from sre_agent.pr.pr_creator import PRCreator
from sre_agent.schemas.pr import PRRequest, PRResult, PRStatus


class _Resp:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *_args, **_kwargs):
        return _Resp(422, json_data=None, text="PR already exists")


def test_create_pr_returns_existing_on_422(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    creator = PRCreator(token="t")

    async def _fake_find(*, request: PRRequest, head_branch: str) -> PRResult:
        return PRResult(
            pr_number=123,
            pr_url="https://github.com/acme/widgets/pull/123",
            status=PRStatus.CREATED,
            branch_name=head_branch,
            base_branch=request.base_branch,
            fix_id=request.fix_id,
            event_id=request.event_id,
            title="Existing",
        )

    monkeypatch.setattr(creator, "find_open_pr_by_head", _fake_find)
    monkeypatch.setattr("sre_agent.pr.pr_creator.httpx.AsyncClient", lambda *a, **k: _Client())

    req = PRRequest(
        fix_id="fix-1",
        event_id=uuid4(),
        repo="acme/widgets",
        base_branch="main",
        diff="diff --git a/x b/x\n",
    )
    res = asyncio.run(creator.create_pr(req, "sre-agent-fix-fix-1"))
    assert res.status == PRStatus.CREATED
    assert res.pr_url and res.pr_url.endswith("/pull/123")
