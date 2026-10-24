"""Two independent quality gates.

`check_quotes` is mechanical: a claimed quote either appears in the source or it
does not. No model, no cost, no judgment — it catches fabricated evidence
outright, which is the failure mode a verifier model is worst at spotting
because it is as willing to believe the quote as the model that invented it.

`verify_with_model` is the judgment gate, and it runs on a *different* prompt
from the one that produced the answer. Self-verification with the same prompt
mostly reproduces the original error.
"""
from __future__ import annotations

import re
from typing import Any

from .structured import ask_structured

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def resolve_path(data: Any, path: str) -> list[tuple[str, Any]]:
    """Resolve a dotted path, where `[]` means "every element".

    `items[].quote` over two items yields [("items[0].quote", ...), ("items[1].quote", ...)].
    """
    results: list[tuple[str, Any]] = [("$", data)]
    for part in path.split("."):
        is_array = part.endswith("[]")
        key = part[:-2] if is_array else part
        next_results: list[tuple[str, Any]] = []
        for prefix, node in results:
            if not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if is_array:
                if isinstance(value, list):
                    next_results += [(f"{prefix}.{key}[{i}]", v) for i, v in enumerate(value)]
            else:
                next_results.append((f"{prefix}.{key}", value))
        results = next_results
    return results


def check_quotes(data: Any, source_text: str, fields: tuple[str, ...]) -> list[str]:
    """Verify every claimed quote appears verbatim in the source.

    Whitespace and case are normalized; nothing else. Loosening this further
    (fuzzy match, token overlap) is how a grounding check quietly stops
    grounding anything.
    """
    if not fields or not source_text:
        return []

    haystack = _normalize(source_text)
    problems: list[str] = []
    for field in fields:
        for location, value in resolve_path(data, field):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{location}: empty quote")
            elif _normalize(value) not in haystack:
                excerpt = value if len(value) <= 60 else value[:57] + "..."
                problems.append(f"{location}: quote not found in source — {excerpt!r}")
    return problems


_VERIFIER_SCHEMA = {
    "type": "object",
    "required": ["verdict", "confidence", "reason"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}


def verify_with_model(provider, *, spec, fields: dict, data: Any, tier: str, trace):
    """Independent review of a candidate answer. Returns (passed, confidence, reason)."""
    import json

    user = (
        f"{spec.render_user(fields)}\n\n"
        f"A candidate answer was produced:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        "Judge it. `confidence` is your probability from 0 to 1 that this answer "
        "is correct and complete. Be strict: an answer that is plausible but "
        "unsupported by the input is a fail."
    )
    verdict, _ = ask_structured(
        provider,
        system=spec.verifier_system,
        user=user,
        schema=_VERIFIER_SCHEMA,
        tier=tier,
        trace=trace,
        node="verify",
        attempts=2,
        max_tokens=1024,
    )
    confidence = float(verdict["confidence"])
    # A verifier that returns 1.0 on everything is a broken verifier; clamp only
    # the range, never the value, so miscalibration stays visible in the eval.
    confidence = min(max(confidence, 0.0), 1.0)
    return verdict["verdict"] == "pass", confidence, verdict["reason"]
