#!/usr/bin/env python3
"""Проверка на десятках тысяч настоящих сообщений с настоящими метками.

Чем это отличается от нашего размеченного набора и зачем нужно и то, и другое.

Размеченный вручную набор — маленький и подробный: у каждого примера свои
маркеры с дословными цитатами, оценка и нужная проверка. На нём калибруется
порог отказа, и больше 120 примеров для этого не требуется.

Этот прогон — другой: берём настоящие сообщения с настоящими метками «спам /
не спам» из публичного корпуса и смотрим, совпадает ли наша оценка с меткой.
Подробностей тут нет, зато есть объём: десятки тысяч, за секунды, без единого
вызова модели.

Честная оговорка, которую надо произносить вслух: **спам — это не мошенничество.**
В корпусе много рекламы, которая не обманывает. Поэтому «не доверять» на спаме
мы считаем попаданием, а на обычном сообщении — ложной тревогой, но знаем,
что часть расхождений — это разница понятий, а не ошибка.

    python3 scripts/eval_at_scale.py --n 25000
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from domains.authenticity import mechanical_answer  # noqa: E402

PARQUET = ("/Users/diana/.cache/huggingface/hub/datasets--benzlokzik--russian-spam-fork"
           "/snapshots/*/processed_combined.parquet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=25000, help="сообщений каждого класса")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print("нужен pandas: pip install pandas pyarrow")
        return 1
    files = glob.glob(PARQUET)
    if not files:
        print("корпус не скачан")
        return 1

    print("читаю корпус…", flush=True)
    df = pd.read_parquet(files[0])
    spam = df[df["label"]].sample(min(args.n, int(df["label"].sum())),
                                  random_state=args.seed)["text"].tolist()
    ham = df[~df["label"]].sample(min(args.n, int((~df["label"]).sum())),
                                  random_state=args.seed)["text"].tolist()
    print(f"взято {len(spam):,} спама и {len(ham):,} обычных\n", flush=True)

    def verdicts(messages: list[str]) -> dict[str, int]:
        out = {"distrust": 0, "doubt": 0, "нет признаков": 0}
        for text in messages:
            answer = mechanical_answer(str(text))
            out["нет признаков" if answer is None else answer["assessment"]] += 1
        return out

    t0 = time.monotonic()
    on_spam = verdicts(spam)
    on_ham = verdicts(ham)
    seconds = time.monotonic() - t0

    caught = on_spam["distrust"] + on_spam["doubt"]
    false_alarm = on_ham["distrust"] + on_ham["doubt"]

    print(f"{'наш ответ':18} {'на спаме':>12} {'на обычных':>14}")
    print("-" * 46)
    for key in ("distrust", "doubt", "нет признаков"):
        label = {"distrust": "не доверять", "doubt": "сомневаться"}.get(key, key)
        print(f"{label:18} {on_spam[key]/len(spam):11.1%} {on_ham[key]/len(ham):13.1%}")
    print("-" * 46)
    print(f"{'подняли тревогу':18} {caught/len(spam):11.1%} {false_alarm/len(ham):13.1%}")

    print(f"\nпросмотрено {len(spam)+len(ham):,} сообщений за {seconds:.1f} с "
          f"({(len(spam)+len(ham))/seconds:,.0f} в секунду), моделей не вызывали")

    try:
        from core.cost import expected_loss
        total = len(spam) + len(ham)
        missed = len(spam) - caught
        loss = expected_loss(missed=missed, false_alarms=false_alarm,
                             abstained=0, total=total)
        print(f"\nожидаемый убыток человека: {loss:,.0f} ₽ на сообщение "
              f"(пропущено {missed:,}, ложных тревог {false_alarm:,})")
        print("для сравнения: система без всякой проверки пропустила бы все "
              f"{len(spam):,} — это {expected_loss(missed=len(spam), false_alarms=0, abstained=0, total=total):,.0f} ₽")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
