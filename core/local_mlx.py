"""Local models on Apple Silicon through MLX.

Why this is worth a tier rather than a curiosity: a local model has zero
marginal cost and no rate limit, so the cheap tier of the cascade becomes free.
The cost column of the eval table stops being a percentage and becomes "the
easy 70% of traffic costs nothing", which is a much stronger claim.

It is also demo insurance. The failure that kills live demos is the API — a
quota, a key, a venue network. A local cheap tier keeps the demo alive when the
network does not.

Two costs, both real: a small quantized model is a weaker extractor than a
frontier model (the schema-retry loop in `core.structured` carries a lot of
weight here), and the weights must be on disk BEFORE the event.

    LLM_BACKEND=mlx     python3 -m evals.run      # both tiers local
    LLM_BACKEND=hybrid  python3 -m evals.run      # local cheap + API strong
"""
from __future__ import annotations

import os
import time

from .providers import PRICES, Completion

# Small enough to be quick on an M-series GPU, big enough to follow a schema.
# Both tiers can point at the same model; the cascade still earns its keep
# through the gates even when it cannot escalate to a larger one.
DEFAULT_CHEAP = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
DEFAULT_STRONG = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"


class MLXProvider:
    """Runs quantized models on the Mac's GPU. Weights load once, per tier."""

    name = "mlx"

    def __init__(self, *, cheap: str | None = None, strong: str | None = None,
                 max_tokens: int = 1024):
        from mlx_lm import generate, load  # lazy: absent MLX must not break anything

        self._load = load
        self._generate = generate
        self._default_max_tokens = max_tokens
        self._models = {
            "cheap": cheap or os.getenv("MLX_MODEL_CHEAP", DEFAULT_CHEAP),
            "strong": strong or os.getenv("MLX_MODEL_STRONG", DEFAULT_STRONG),
        }
        self._loaded: dict[str, tuple] = {}
        # Local inference has no per-token price. Registering zeros keeps the
        # cost column honest rather than crashing the price lookup.
        for model in self._models.values():
            PRICES.setdefault(model, (0.0, 0.0))

    def model_for(self, tier: str) -> str:
        return self._models[tier]

    def _ensure(self, tier: str):
        name = self._models[tier]
        if name not in self._loaded:
            self._loaded[name] = self._load(name)
        return self._loaded[name]

    def complete(self, *, system, user, tier="cheap", schema=None, max_tokens=None) -> Completion:
        model, tokenizer = self._ensure(tier)
        prompt = tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            add_generation_prompt=True,
            tokenize=False,
        )
        started = time.monotonic()
        text = self._generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens or self._default_max_tokens,
            verbose=False,
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        return Completion(
            text=text,
            model=self._models[tier],
            input_tokens=len(tokenizer.encode(prompt)),
            output_tokens=len(tokenizer.encode(text)),
            latency_ms=latency_ms,
        )
