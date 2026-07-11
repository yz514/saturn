"""Deterministic offline LLM client for --mock runs and tests."""

from __future__ import annotations

import json

_ANALYSIS = json.dumps(
    {
        "executive_summary": "[MOCK] Executive summary placeholder for offline/testing mode.",
        "company_overview": "[MOCK] Company overview placeholder.",
        "business_segments": "[MOCK] Business segments placeholder.",
        "financial_snapshot": "[MOCK] Financial commentary placeholder.",
        "valuation_discussion": "[MOCK] Valuation discussion placeholder.",
        "key_risks": "[MOCK] Key risks placeholder.",
        "open_questions": "[MOCK] Open questions placeholder.",
    }
)

_DEBATE = json.dumps(
    {
        "bull_thesis": "[MOCK] Bull thesis placeholder.",
        "bear_thesis": "[MOCK] Bear thesis placeholder.",
        "final_view": "[MOCK] Balanced final view placeholder.",
    }
)

_CRITIC = json.dumps({"claims_checked": 0, "summary": "[MOCK] verification placeholder.", "findings": []})


class MockLLMClient:
    """Returns fixed JSON keyed by the OUTPUT_SCHEMA tag in the prompt."""

    def complete(
        self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000
    ) -> str:
        if "OUTPUT_SCHEMA=debate" in prompt:
            return _DEBATE
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return _ANALYSIS
        if "OUTPUT_SCHEMA=critic" in prompt:
            return _CRITIC
        return "{}"
