"""Local models on Windows and Linux through Ollama.

The kit's local tier is MLX, which runs only on Apple Silicon. Organizers give
no API, so a Windows laptop with an NVIDIA card needs its own local path — and
it must not touch `core/`. `core.providers.register_backend` exists for exactly
this: the provider lives here and registers itself on import.

Stdlib only (`urllib`), no new dependency. Ollama's `format` takes the JSON
Schema, so a 7B model is constrained to valid JSON at decode time;
`core.structured` still validates and retries, so the schema gate stays in the
trace either way.

    ollama pull qwen2.5:7b
    set LLM_BACKEND=ollama
    uvicorn api.server:app --port 8000

    python -m api.ollama       # one real call per tier: is the model up and fast enough
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from core.providers import PRICES, Completion, register_backend

# Fits in 8 GB of VRAM with room to spare. Both tiers on one model means no
# swapping weights between calls; the cascade still earns its keep through the
# gates (decision 002: both tiers local on 7B when there is no paid model).
DEFAULT_CHEAP = "qwen2.5:7b"
DEFAULT_STRONG = "qwen2.5:7b"


class OllamaProvider:
    """Talks to a local Ollama server over HTTP. Weights stay loaded between calls."""

    name = "ollama"

    def __init__(self, *, cheap: str | None = None, strong: str | None = None,
                 host: str | None = None, timeout_s: float = 180.0):
        self._host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self._host.startswith("http"):
            self._host = "http://" + self._host
        self._timeout_s = timeout_s
        self._models = {
            "cheap": cheap or os.getenv("OLLAMA_MODEL_CHEAP", DEFAULT_CHEAP),
            "strong": strong or os.getenv("OLLAMA_MODEL_STRONG", DEFAULT_STRONG),
        }
        # Local inference has no per-token price. Registering zeros keeps the
        # cost column honest rather than crashing the price lookup.
        for model in self._models.values():
            PRICES.setdefault(model, (0.0, 0.0))

    def model_for(self, tier: str) -> str:
        return self._models[tier]

    def complete(self, *, system, user, tier="cheap", schema=None, max_tokens=1024) -> Completion:
        model = self._models[tier]
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            # Never unload: a cold load takes ~15 s on top of the answer and
            # blows MODEL_DEADLINE, so the first check after idle went mechanical.
            "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192},
        }
        if schema is not None:
            body["format"] = schema
        req = urllib.request.Request(
            f"{self._host}/api/chat", json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                out = json.load(resp)
        except urllib.error.HTTPError as exc:
            # 404 here almost always means the model was never pulled.
            raise RuntimeError(f"ollama {model}: HTTP {exc.code} {exc.read().decode(errors='replace')}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            # RuntimeError on purpose: api.server falls back to the mechanical
            # answer instead of a 500 when the model is down.
            raise RuntimeError(f"ollama at {self._host} unreachable: {exc}") from exc
        latency_ms = (time.monotonic() - started) * 1000.0
        return Completion(
            text=out["message"]["content"],
            model=model,
            input_tokens=out.get("prompt_eval_count", 0),
            output_tokens=out.get("eval_count", 0),
            latency_ms=latency_ms,
        )


register_backend("ollama", lambda **kw: OllamaProvider(**kw))


if __name__ == "__main__":
    provider = OllamaProvider()
    for tier in ("cheap", "strong"):
        c = provider.complete(
            system="Отвечай только JSON.",
            user='Оцени сообщение «Мам, это я, скинь 15 000 на новый номер». '
                 'Верни {"assessment": "trust" | "doubt" | "distrust"}.',
            tier=tier,
            schema={"type": "object", "required": ["assessment"],
                    "properties": {"assessment": {"type": "string",
                                                  "enum": ["trust", "doubt", "distrust"]}}},
            max_tokens=64,
        )
        print(f"{tier:6} {c.model:20} {c.latency_ms:7.0f} мс  "
              f"{c.output_tokens} ток. -> {c.text.strip()}")
