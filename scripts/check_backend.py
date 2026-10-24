#!/usr/bin/env python3
"""Один настоящий вызов через настроенный бэкенд.

Нужен ровно для одного: убедиться, что ключ, модель и сеть работают, ДО того
как от этого зависит демо. Путь, который ни разу не выполнялся, на хакатоне
считается неработающим.

    python3 scripts/check_backend.py
    LLM_BACKEND=anthropic python3 scripts/check_backend.py
    LLM_BACKEND=mlx       python3 scripts/check_backend.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Запуск идёт из scripts/, поэтому корень проекта на sys.path не попадает сам.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.providers import get_provider  # noqa: E402
from core.structured import extract_json  # noqa: E402
from domains.commitments import load_cases, make_mock_handler  # noqa: E402

SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


def main() -> int:
    backend = os.getenv("LLM_BACKEND", "mock")
    print(f"бэкенд: {backend}")
    try:
        provider = get_provider(mock_handler=make_mock_handler(load_cases()))
    except Exception as exc:  # noqa: BLE001 - показать причину, а не трассировку
        print(f"ПРОВАЛ: провайдер не создался — {type(exc).__name__}: {exc}")
        return 1

    for tier in ("cheap", "strong"):
        model = provider.model_for(tier)
        try:
            if backend == "mock":
                # У заглушки нет свободных ответов: берём настоящую фикстуру.
                case = load_cases()[0]
                completion = provider.complete(
                    system="проверка", user=f"Transcript:\n---\n{case['text']}\n---",
                    tier=tier, schema=None,
                )
            else:
                completion = provider.complete(
                    system="Отвечай одним JSON-объектом и ничем больше.",
                    user='Верни ровно {"ok": true}',
                    tier=tier, schema=SCHEMA, max_tokens=64,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"ПРОВАЛ  {tier:6} {model}: {type(exc).__name__}: {exc}")
            return 1

        parsed = ""
        if backend != "mock":
            try:
                parsed = f" -> {extract_json(completion.text)}"
            except Exception:  # noqa: BLE001 - модель ответила, но не JSON
                parsed = f" -> не JSON: {completion.text[:60]!r}"

        print(
            f"ОК      {tier:6} {model}: {completion.latency_ms:.0f} мс, "
            f"{completion.input_tokens}→{completion.output_tokens} токенов, "
            f"${completion.cost_usd:.5f}{parsed}"
        )

    print("\nбэкенд рабочий")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
