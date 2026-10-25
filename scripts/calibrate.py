#!/usr/bin/env python3
"""Подбирает порог отказа под заданную долю ошибок.

    python3 scripts/calibrate.py            # не больше 5% неверных среди выданных
    python3 scripts/calibrate.py --alpha 0.1

Как работает: прогоняет каскад с порогом 0 (то есть отвечает всегда) и
записывает пару «уверенность проверяющей модели — верен ли ответ». Затем делит
примеры пополам: на первой половине подбирает порог с гарантией, на второй
проверяет, держится ли гарантия на данных, которые в подборе не участвовали.

Так порог перестаёт быть взятым с потолка числом и становится утверждением,
которое можно защитить: «при пороге 0,72 среди выданных ответов не больше 5%
неверных, проверено на отложенной выборке».
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.calibrate import Record, calibrate  # noqa: E402
from core.pipeline import run  # noqa: E402
from core.providers import get_provider  # noqa: E402
from domains.authenticity import SPEC, load_cases, make_mock_handler  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="допустимая доля неверных среди выданных ответов")
    parser.add_argument("--confidence", type=float, default=0.95,
                        help="надёжность самой границы")
    args = parser.parse_args()

    cases = load_cases()
    provider = get_provider(mock_handler=make_mock_handler(cases))

    records: list[Record] = []
    for case in cases:
        fields = {"text": case["text"]}
        # Порог 0 — отвечаем всегда: нужны пары «уверенность / верность» по всем
        # примерам, иначе калибровать нечего.
        result = run(SPEC, fields, provider, floor=0.0)
        if not result.answered:
            continue
        records.append(Record(
            confidence=result.confidence,
            correct=bool(SPEC.grade(case["expected"], result.data)),
        ))

    if len(records) < 8:
        print(f"слишком мало ответов для калибровки: {len(records)}")
        return 1

    # Чередование вместо случайного разбиения: воспроизводимо без seed и не
    # зависит от того, как кейсы упорядочены по сложности в файле.
    cal = records[0::2]
    test = records[1::2]

    print(f"ответов всего: {len(records)}  (калибровка {len(cal)}, проверка {len(test)})\n")
    try:
        result = calibrate(cal, alpha=args.alpha, test=test, confidence=args.confidence)
    except ValueError as exc:
        print(f"не вышло: {exc}")
        return 1

    print(result.report())
    print(f"\nвписать в .env:  CONFIDENCE_FLOOR={result.threshold:.2f}")
    if result.test_error_rate is not None and result.test_error_rate > args.alpha:
        print("\nВНИМАНИЕ: на отложенной выборке доля ошибок выше цели.")
        print("  Гарантия вероятностная, а примеров мало — не выдавайте её за точную.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
