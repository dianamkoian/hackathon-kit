#!/usr/bin/env python3
"""Проверка разметки до того, как она попадёт в набор.

    python3 evals/разметка/check_labels.py evals/разметка/соня.jsonl

Строка — в том же формате, что в evals/cases_authenticity.jsonl. Проверяет
код, а не глаза: поля и `mock`, схему ответа спеки, дословность каждой цитаты,
повтор id. Отдельно показывает, что сделают с эталоном механические части
каскада: барьер `signals` и ответ без модели. Их расхождение с эталоном —
не ошибка разметки, а находка про барьер.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.structured import validate  # noqa: E402
from core.verify import check_quotes, check_signals  # noqa: E402
from domains.authenticity import SPEC, load_cases, mechanical_answer  # noqa: E402

FIELDS = ("id", "difficulty", "source", "text", "expected", "mock")
MOCK_MODES = {"correct", "wrong", "fabricate"}


def problems_of(row: dict, known_ids: set[str]) -> list[str]:
    problems = [f"нет поля {key}" for key in FIELDS if key not in row]
    if problems:
        return problems
    if row["id"] in known_ids:
        problems.append("id уже есть в наборе")
    mock = row["mock"]
    if set(mock) != {"cheap", "strong"} or not set(mock.values()) <= MOCK_MODES:
        problems.append(f"mock: {mock}")
    problems += validate(row["expected"], SPEC.schema)
    problems += check_quotes(row["expected"], row["text"], SPEC.quote_fields)
    return problems


def main(path: str) -> int:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    known = {case["id"] for case in load_cases()}
    seen: set[str] = set()
    failed = 0
    for row in rows:
        problems = problems_of(row, known | seen)
        seen.add(row.get("id", ""))
        failed += bool(problems)
        print(f"{row.get('id', '?')}: {'ошибки' if problems else 'ok'}")
        for problem in problems:
            print(f"    {problem}")
        if problems:
            continue

        gate, _ = check_signals(
            row["expected"], row["text"], SPEC.signal_markers,
            type_field=SPEC.signal_type_field, verdict_field=SPEC.signal_verdict_field,
            unsafe_verdicts=SPEC.signal_unsafe_verdicts,
        )
        if gate:
            print(f"    барьер signals отклонит эталон: {'; '.join(gate)}")
        mechanical = mechanical_answer(row["text"])
        if mechanical and mechanical.get("assessment") != row["expected"]["assessment"]:
            print(f"    ответ без модели: {mechanical.get('assessment')}, "
                  f"а в эталоне {row['expected']['assessment']}")

    print(f"\nстрок: {len(rows)}, с ошибками разметки: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
