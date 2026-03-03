"""Plan critic for hallucination-risk and reasoning checks."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from sre_agent.ai.llm_provider import LLMProvider, get_llm_provider
from sre_agent.ai.prompt_builder import PromptBuilder
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.critic import CriticDecision, CriticParseError
from sre_agent.schemas.fix_plan import FixPlan
from sre_agent.schemas.intelligence import RCAResult

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return raw
    return raw[start : end + 1]


def _repair_prompt(error: str, bad_output: str) -> str:
    return (
        "Return JSON ONLY. Do not include markdown. Do not include commentary.\n\n"
        "The previous critic output was invalid.\n\n"
        f"Error:\n{error}\n\n"
        f"Previous output:\n{bad_output}\n\n"
        "Return a single corrected JSON object that matches the required schema."
    )


class PlanCritic:
    """Generates deterministic critic decisions from plan/context/rca."""

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        prompt_builder: PromptBuilder | None = None,
        max_retries: int = 2,
    ) -> None:
        self._llm_provider = llm_provider
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.max_retries = max_retries
        self.last_raw_output: str | None = None
        self.last_model_name: str | None = None

    async def review(
        self,
        *,
        rca_result: RCAResult,
        context: FailureContextBundle,
        plan: FixPlan,
        max_tokens: int = 900,
    ) -> CriticDecision:
        provider = self._llm_provider or get_llm_provider()
        plan_json = plan.model_dump_json(indent=2)
        prompt = self.prompt_builder.build_critic_prompt(
            rca_result=rca_result,
            context=context,
            plan_json=plan_json,
        )

        last_error: str | None = None
        last_raw: str | None = None

        for attempt in range(self.max_retries + 1):
            use_prompt = (
                prompt if attempt == 0 else _repair_prompt(last_error or "unknown", last_raw or "")
            )
            async with provider:
                raw = await provider.generate(
                    prompt=use_prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            self.last_raw_output = raw
            last_raw = raw
            json_text = _extract_json_object(raw)
            try:
                data = json.loads(json_text)
            except Exception as exc:
                last_error = f"JSON parse error: {exc}"
                continue

            try:
                decision = CriticDecision.model_validate(data)
            except ValidationError as exc:
                last_error = f"Schema validation error: {exc}"
                continue

            logger.info(
                "Generated critic decision",
                extra={
                    "allowed": decision.allowed,
                    "requires_manual_review": decision.requires_manual_review,
                    "hallucination_risk": decision.hallucination_risk,
                    "issues": len(decision.issues),
                    "attempt": attempt,
                    "model": provider.model_name,
                },
            )
            self.last_model_name = provider.model_name
            return decision

        raise CriticParseError(
            message=last_error or "Failed to generate valid critic decision",
            raw_output=last_raw or "",
        )
