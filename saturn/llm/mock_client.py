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

_ALPHA = json.dumps(
    {
        "stance": "in_line_consensus",
        "variant": "[MOCK] No differentiated view in offline mode.",
        "rationale": "[MOCK] Placeholder rationale.",
        "confidence": "low",
        "key_variable": "[MOCK] key variable",
        "falsifier": "[MOCK] observable event within 2 quarters",
        "horizon": "next 2 quarters",
        "scenarios": [
            {"name": "bull", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 175.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 5.0, "multiple": 160.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 3.5, "multiple": 130.0, "multiple_basis": "P/E"},
        ],
    }
)


class MockLLMClient:
    """Returns fixed JSON keyed by the OUTPUT_SCHEMA tag in the prompt."""

    def complete(
        self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000
    ) -> str:
        if "OUTPUT_SCHEMA=alpha" in prompt:
            return _ALPHA
        if "OUTPUT_SCHEMA=debate" in prompt:
            return _DEBATE
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return _ANALYSIS
        if "OUTPUT_SCHEMA=critic" in prompt:
            return _CRITIC
        return "{}"
