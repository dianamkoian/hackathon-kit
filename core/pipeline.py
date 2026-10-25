"""The cascade: cheap tier -> gates -> escalate -> abstain.

The shape is the argument you make to judges. A single strong model answering
everything is the baseline; this is cheaper on the easy mass, more accurate
where it matters, and — the part nobody else shows — it declines instead of
guessing when neither tier convinces the verifier.

Domain-independent by construction: a theme supplies a `TaskSpec` and the
cascade, trace, eval harness and API work unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .structured import StructuredError, ask_structured
from .trace import Trace
from .verify import check_quotes, verify_with_model


@dataclass
class TaskSpec:
    """Everything a theme has to supply. The only file you rewrite on the day."""

    name: str
    system: str
    user_template: str
    schema: dict
    verifier_system: str
    # Dotted paths whose values must appear verbatim in the source text,
    # e.g. ("items[].quote",). Empty disables the mechanical gate.
    quote_fields: tuple[str, ...] = ()
    # Which key of `fields` holds the text quotes are checked against.
    source_field: str | None = None
    # Что вообще может считаться пропуском. Пустой кортеж = любой текст, и
    # тогда проверяющая модель объявляет пропуском «Добрый день!», а код честно
    # отказывается отвечать. Домен обязан назвать свой словарь.
    omission_types: tuple[str, ...] = ()
    # Запрещать ли ответ из-за пропуска. Последствие пропуска зависит от задачи:
    # пропущенное обязательство — это неверно поставленная человеку задача, и там
    # вето уместно. Пропущенный один приём манипуляции из пяти — всё ещё полезный
    # ответ, и глушить его нельзя. Замерено: с вето модель на 7B отказывается
    # отвечать и на мошенническом тексте, и на безобидном.
    omission_is_veto: bool = False
    # Tier the verifier runs on. Cheap by default and deliberately so: judging a
    # finished answer is easier than producing one, and a verifier on the strong
    # tier costs as much as the answer it is checking — which wipes out the
    # cascade's cost advantage on exactly the cases that escalate. The tradeoff
    # is a weaker judge, so watch what the eval says about waved-through errors.
    verifier_tier: str = "cheap"
    # fields -> ordered tiers to try. Default: cheap first, strong on failure.
    router: Callable[[dict], list[str]] | None = None
    # (expected, produced) -> correct? Used by the eval harness only.
    grade: Callable[[Any, Any], bool] | None = None

    def render_user(self, fields: dict) -> str:
        return self.user_template.format(**fields)

    def route(self, fields: dict) -> list[str]:
        return self.router(fields) if self.router else ["cheap", "strong"]


@dataclass
class Result:
    status: str  # "ok" | "escalated" | "abstained"
    data: Any | None
    confidence: float
    reason: str
    trace: Trace
    tier_used: str | None = None

    @property
    def answered(self) -> bool:
        return self.status != "abstained"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "answered": self.answered,
            "data": self.data,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "tier_used": self.tier_used,
            "trace": self.trace.to_dict(),
        }


def confidence_floor() -> float:
    return float(os.getenv("CONFIDENCE_FLOOR", "0.6"))


def run(spec: TaskSpec, fields: dict, provider, *, floor: float | None = None,
        trace: Trace | None = None) -> Result:
    """Run the cascade. Never raises on model failure — it abstains instead."""
    floor = confidence_floor() if floor is None else floor
    trace = trace or Trace()
    source_text = fields.get(spec.source_field, "") if spec.source_field else ""
    tiers = spec.route(fields)
    last_reason = "no tier produced a verified answer"

    for position, tier in enumerate(tiers):
        escalated = position > 0
        trace.add("route", True, detail=f"tier={tier}" + (" (escalated)" if escalated else ""))

        try:
            data, _ = ask_structured(
                provider,
                system=spec.system,
                user=spec.render_user(fields),
                schema=spec.schema,
                tier=tier,
                trace=trace,
                node="solve",
            )
        except StructuredError as exc:
            last_reason = f"invalid output: {exc}"
            continue

        quote_problems = check_quotes(data, source_text, spec.quote_fields)
        if spec.quote_fields:
            trace.add(
                "ground",
                not quote_problems,
                detail="all quotes found in source" if not quote_problems
                else "; ".join(quote_problems[:3]),
            )
        if quote_problems:
            last_reason = f"ungrounded: {quote_problems[0]}"
            continue

        try:
            passed, confidence, reason = verify_with_model(
                provider, spec=spec, fields=fields, data=data,
                tier=spec.verifier_tier, trace=trace,
            )
        except StructuredError as exc:
            # A verifier we cannot read is a failed gate, not a pass.
            trace.add("verify", False, detail=f"verifier unreadable: {exc}")
            last_reason = "verifier unreadable"
            continue

        accepted = passed and confidence >= floor
        trace.add(
            "gate", accepted,
            detail=f"verdict={'pass' if passed else 'fail'} "
                   f"confidence={confidence:.2f} floor={floor:.2f}",
        )
        if accepted:
            return Result(
                status="escalated" if escalated else "ok",
                data=data, confidence=confidence, reason=reason,
                trace=trace, tier_used=tier,
            )
        last_reason = reason or "below confidence floor"

    trace.add("abstain", True, detail=last_reason)
    return Result(status="abstained", data=None, confidence=0.0,
                  reason=last_reason, trace=trace)


def run_baseline(spec: TaskSpec, fields: dict, provider, *, trace: Trace | None = None) -> Result:
    """One prompt, one strong model, no gates — the comparison judges will ask for.

    Deliberately given the same prompt and the strong tier. Handicapping the
    baseline (weaker model, worse prompt, no retries at all) is the usual way
    these tables lie, and it is the first thing a sharp judge probes.
    """
    trace = trace or Trace()
    try:
        data, _ = ask_structured(
            provider,
            system=spec.system,
            user=spec.render_user(fields),
            schema=spec.schema,
            tier="strong",
            trace=trace,
            node="baseline",
            attempts=1,
        )
    except StructuredError as exc:
        trace.add("baseline", False, detail=str(exc))
        return Result("abstained", None, 0.0, f"invalid output: {exc}", trace)
    return Result("ok", data, 1.0, "no verification in baseline", trace, "strong")
