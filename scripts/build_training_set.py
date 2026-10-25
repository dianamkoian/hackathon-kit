#!/usr/bin/env python3
"""Собирает набор для тренировки из настоящих сообщений.

Тренировка учит человека замечать приёмы самому — а учиться надо на
настоящем. Берём сообщения из публичного корпуса с настоящими метками и
оставляем те, где правильный ответ не вызывает спора:

  обман     — помечено спамом И детекторы нашли признак;
  нормально — помечено обычным И детекторы молчат.

Спорные случаи (спам без признаков, обычное с признаком) в тренировку не
попадают: на них человек учился бы неправильному.

Набор маленький и лежит в репозитории, чтобы демо не тянуло корпус на 460 МБ.

    python3 scripts/build_training_set.py --n 120
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.verify import find_mechanical_signals  # noqa: E402
from domains.authenticity import mechanical_answer  # noqa: E402

PARQUET = ("/Users/diana/.cache/huggingface/hub/datasets--benzlokzik--russian-spam-fork"
           "/snapshots/*/processed_combined.parquet")
OUT = ROOT / "evals" / "training.jsonl"

MIN_LEN, MAX_LEN = 40, 600


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=120, help="сколько всего примеров")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    import pandas as pd
    files = glob.glob(PARQUET)
    if not files:
        print("корпус не скачан")
        return 1

    df = pd.read_parquet(files[0])
    random.seed(args.seed)
    half = args.n // 2

    def pick(is_spam: bool, want: int) -> list[dict]:
        pool = df[df["label"] == is_spam].sample(
            min(40000, int((df["label"] == is_spam).sum())), random_state=args.seed)
        out = []
        for text in pool["text"]:
            text = str(text).strip()
            if not (MIN_LEN <= len(text) <= MAX_LEN):
                continue
            signals = find_mechanical_signals(text)
            if is_spam and not signals:
                continue          # спам без признаков — спорно, не учим на нём
            if not is_spam and signals:
                continue          # обычное с признаком — тоже спорно
            answer = mechanical_answer(text) if is_spam else None
            out.append({
                "id": f"t-{'s' if is_spam else 'h'}{len(out)+1:03d}",
                "source": "real-corpus",
                "text": text,
                "truth": "обман" if is_spam else "нормально",
                "markers": (answer or {}).get("markers", []),
            })
            if len(out) >= want:
                break
        return out

    rows = pick(True, half) + pick(False, args.n - half)
    random.shuffle(rows)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    scam = sum(1 for r in rows if r["truth"] == "обман")
    print(f"собрано {len(rows)} примеров ({scam} обманных, {len(rows)-scam} обычных)")
    print(f"файл: {OUT} — {OUT.stat().st_size/1024:.0f} КБ")
    print("\nпримеры:")
    for r in rows[:3]:
        print(f"  [{r['truth']:9}] {r['text'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
