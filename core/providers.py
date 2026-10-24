"""Model access behind one seam.

This file exists for one hackathon failure mode: organizers mandating a model or
an endpoint with two hours left. Everything else in the kit talks to `Provider`,
so a swap is a change here and nowhere else.

Backends:
  mock      — deterministic, offline, no key, no install. Default.
  anthropic — real calls through the official SDK.

Add a third by writing a class with `.model_for()` and `.complete()` and
registering it in `_BACKENDS`.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

# USD per 1M tokens (input, output). Anthropic first-party rates.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    # The mock priced like the real tiers, so offline numbers stay meaningful.
    "mock-cheap": (0.10, 0.50),
    "mock-strong": (4.00, 20.00),
}


def price_of(model: str) -> tuple[float, float]:
    if model not in PRICES:
        # Unknown model: cost reporting would silently read as free, which is
        # worse than a loud failure when the whole pitch is a cost table.
        raise KeyError(
            f"no price for {model!r}; add it to core.providers.PRICES "
            f"(known: {', '.join(sorted(PRICES))})"
        )
    return PRICES[model]


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def cost_usd(self) -> float:
        in_price, out_price = price_of(self.model)
        return (self.input_tokens * in_price + self.output_tokens * out_price) / 1_000_000


class Provider(Protocol):
    name: str

    def model_for(self, tier: str) -> str: ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        tier: str = "cheap",
        schema: dict | None = None,
        max_tokens: int = 4096,
    ) -> Completion: ...


# --------------------------------------------------------------------------- mock

MockHandler = Callable[[str, str, str, dict | None], str]
"""(system, user, model, schema) -> response text."""


class MockProvider:
    """Offline backend. Makes the eval harness runnable before any key exists.

    Not a stub for its own sake: the eval table, the trace view and the API all
    work on it, so the repo is demonstrable from minute one and the cascade's
    escalation logic is testable without spending money.
    """

    name = "mock"

    def __init__(self, handler: MockHandler, *, latency_ms: dict[str, float] | None = None):
        self.handler = handler
        # Rough stand-ins so p95 comparisons mean something offline.
        self.latency_ms = latency_ms or {"cheap": 300.0, "strong": 1400.0}

    def model_for(self, tier: str) -> str:
        return {"cheap": "mock-cheap", "strong": "mock-strong"}[tier]

    def complete(self, *, system, user, tier="cheap", schema=None, max_tokens=4096) -> Completion:
        model = self.model_for(tier)
        text = self.handler(system, user, model, schema)
        # ~4 chars per token is close enough for relative cost comparisons.
        return Completion(
            text=text,
            model=model,
            input_tokens=(len(system) + len(user)) // 4,
            output_tokens=len(text) // 4,
            latency_ms=self.latency_ms[tier],
        )


# ---------------------------------------------------------------------- anthropic


class AnthropicProvider:
    """Real calls through the official SDK.

    Two deliberate choices, both about surviving a deadline:

    1. The SDK is imported lazily, so the kit runs with nothing installed.
    2. Native structured output (`output_config.format`) is attempted once; if
       the API rejects the parameter, we fall back to prompt-level JSON for the
       rest of the run. `core.structured` validates and retries either way, so
       correctness does not depend on which path we are on.
    """

    name = "anthropic"

    def __init__(self, *, api_key: str | None = None):
        import anthropic  # lazy: absent install must not break the mock path

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._models = {
            "cheap": os.getenv("MODEL_CHEAP", "claude-haiku-5-5"),
            "strong": os.getenv("MODEL_STRONG", "claude-opus-5-5"),
        }
        self._effort = {
            "cheap": os.getenv("EFFORT_CHEAP", "low"),
            "strong": os.getenv("EFFORT_STRONG", "medium"),
        }
        self._native_format = True

    def model_for(self, tier: str) -> str:
        return self._models[tier]

    def complete(self, *, system, user, tier="cheap", schema=None, max_tokens=4096) -> Completion:
        model = self.model_for(tier)
        output_config: dict[str, Any] = {"effort": self._effort[tier]}
        if schema is not None and self._native_format:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        started = time.monotonic()
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except self._anthropic.BadRequestError as exc:
            if schema is None or not self._native_format or "output_config" not in str(exc):
                raise
            # Parameter shape moved under us. Degrade once, loudly, and keep going:
            # core.structured still validates and retries on the prompt-only path.
            print(f"[providers] native structured output rejected, "
                  f"falling back to prompt-level JSON: {exc}")
            self._native_format = False
            return self.complete(system=system, user=user, tier=tier,
                                 schema=schema, max_tokens=max_tokens)

        latency_ms = (time.monotonic() - started) * 1000.0

        if resp.stop_reason == "refusal":
            detail = getattr(resp.stop_details, "category", None)
            raise RuntimeError(f"model declined the request (category={detail})")

        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(
            text=text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_ms=latency_ms,
        )


# -------------------------------------------------------------------- composite


class HybridProvider:
    """A different provider per tier.

    The arrangement worth showing a jury: a local model carrying the easy
    majority at zero marginal cost, an API model reserved for escalations.
    "Optimal choice of models" stops being a claim and becomes a line in the
    cost table.
    """

    name = "hybrid"

    def __init__(self, cheap: Provider, strong: Provider):
        self._by_tier = {"cheap": cheap, "strong": strong}

    def model_for(self, tier: str) -> str:
        return self._by_tier[tier].model_for(tier)

    def complete(self, *, system, user, tier="cheap", schema=None, max_tokens=4096) -> Completion:
        return self._by_tier[tier].complete(
            system=system, user=user, tier=tier, schema=schema, max_tokens=max_tokens
        )


# ----------------------------------------------------------------------- factory


def _mlx(**kw) -> Provider:
    from .local_mlx import MLXProvider

    return MLXProvider(**kw)


def _hybrid(**kw) -> Provider:
    """Local cheap tier, API strong tier."""
    from .local_mlx import MLXProvider

    return HybridProvider(cheap=MLXProvider(), strong=AnthropicProvider(**kw))


_BACKENDS: dict[str, Callable[..., Provider]] = {
    "anthropic": lambda **kw: AnthropicProvider(**kw),
    "mlx": _mlx,
    "hybrid": _hybrid,
}


def register_backend(name: str, factory: Callable[..., Provider]) -> None:
    """Hook for a provider the organizers hand you mid-hackathon."""
    _BACKENDS[name] = factory


def get_provider(mock_handler: MockHandler | None = None, **kw) -> Provider:
    """Build the backend named by LLM_BACKEND (default: mock)."""
    backend = os.getenv("LLM_BACKEND", "mock")
    if backend == "mock":
        if mock_handler is None:
            raise ValueError("LLM_BACKEND=mock needs a mock_handler (see domains/)")
        return MockProvider(mock_handler)
    if backend not in _BACKENDS:
        raise ValueError(
            f"unknown LLM_BACKEND={backend!r}; "
            f"known: mock, {', '.join(sorted(_BACKENDS))}"
        )
    return _BACKENDS[backend](**kw)
