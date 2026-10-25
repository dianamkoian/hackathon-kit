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


# Порядок полей здесь — часть метода, а не оформление. Модель генерирует JSON
# слева направо, поэтому перечисление найденного идёт ДО вердикта: пока она не
# выписала, что видит в источнике, ей нечем заметить пропуск.
_VERIFIER_SCHEMA = {
    "type": "object",
    "required": ["found_in_source", "missing_from_answer", "verdict", "confidence", "reason"],
    "properties": {
        "found_in_source": {"type": "array", "items": {"type": "string"}},
        "missing_from_answer": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}


def _real_omissions(claimed, spec, data) -> list[str]:
    """Отсеивает мнимые пропуски.

    Решающая модель часто цитирует шире, чем проверяющая: «скрипт готов, я
    запущу его завтра» против «я запущу его завтра». Проверяющая сверяет
    строки и объявляет своё короткое цитирование пропущенным, хотя
    обязательство в ответе есть.

    Сверку делает код, а не модель: пересекается ли заявленный пропуск хоть
    с одной цитатой в ответе. Перечислять модель умеет, аккуратно сверять
    строки — нет.
    """
    present = [
        _normalize(value)
        for field in spec.quote_fields
        for _, value in resolve_path(data, field)
        if isinstance(value, str) and value.strip()
    ]

    real: list[str] = []
    for item in claimed:
        text = _normalize(str(item))
        if not text:
            continue
        if any(text in quote or quote in text for quote in present):
            continue  # обязательство в ответе есть, разошлись только границы цитаты
        real.append(str(item))
    return real


def verify_with_model(provider, *, spec, fields: dict, data: Any, tier: str, trace):
    """Independent review of a candidate answer. Returns (passed, confidence, reason)."""
    import json

    user = (
        f"{spec.render_user(fields)}\n\n"
        f"A candidate answer was produced:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        "Review it in this order, and do not skip a step:\n"
        "1. `found_in_source`: quote EVERY statement in the input above that "
        "qualifies, working through the input from start to end. Do this before "
        "you look at the candidate answer again.\n"
        "2. `missing_from_answer`: list the ones from step 1 that the candidate "
        "answer does not contain. An answer that stops after the first item is "
        "the most common failure — check the end of the input specifically.\n"
        "3. `verdict`, `confidence` (0 to 1), `reason`.\n"
        "Be strict: plausible but unsupported is a fail."
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

    passed = verdict["verdict"] == "pass"
    reason = verdict["reason"]

    # The consequence of an omission is enforced in code, not left to the model.
    # Measured: a verifier asked only for a verdict calls an answer "an exact
    # match" while a whole item is missing from it. Asking it to enumerate what
    # is absent is what makes the omission visible; refusing to pass a non-empty
    # list is what makes it matter.
    missing = _real_omissions(verdict.get("missing_from_answer", []), spec, data)
    if missing:
        passed = False
        confidence = min(confidence, 0.3)
        reason = f"пропущено: {'; '.join(str(m)[:60] for m in missing[:3])}"

    trace.add("recall", not missing,
              detail="ничего не пропущено" if not missing else f"пропусков: {len(missing)}")
    return passed, confidence, reason
