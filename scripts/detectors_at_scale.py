#!/usr/bin/env python3
"""Проверка детекторов на десятках тысяч настоящих сообщений.

Зачем. Размеченных вручную примеров не бывает тысяч, и для калибровки порога
их столько не нужно. Но у механических детекторов другая природа: они не
угадывают, а наблюдают, поэтому их можно и нужно проверять на объёме —
без разметки и без единого вызова модели.

Что считаем:
  - как часто каждый детектор срабатывает на спаме и на обычных сообщениях;
  - во сколько раз чаще на спаме — это и есть мера полезности признака;
  - сколько сообщений вообще остаются без признаков.

Источник: benzlokzik/russian-spam-fork (CC BY-NC 4.0), 4,5 млн сообщений,
361 тыс. помечены как спам. Лицензия некоммерческая — для хакатона годится,
в продукт такие данные не кладут.

    python3 scripts/detectors_at_scale.py --n 20000
"""
from __future__ import annotations

import argparse
import glob
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.verify import find_mechanical_signals  # noqa: E402

PARQUET = ("/Users/diana/.cache/huggingface/hub/datasets--benzlokzik--russian-spam-fork"
           "/snapshots/*/processed_combined.parquet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=20000,
                        help="сколько сообщений каждого класса взять")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print("нужен pandas: pip install pandas pyarrow")
        return 1

    files = glob.glob(PARQUET)
    if not files:
        print("набор не скачан; см. строку PARQUET в этом файле")
        return 1

    print("читаю набор…", flush=True)
    df = pd.read_parquet(files[0])
    random.seed(args.seed)

    spam = df[df["label"]].sample(min(args.n, int(df["label"].sum())),
                                  random_state=args.seed)["text"].tolist()
    ham = df[~df["label"]].sample(min(args.n, int((~df["label"]).sum())),
                                  random_state=args.seed)["text"].tolist()
    print(f"взято: {len(spam):,} спама и {len(ham):,} обычных\n", flush=True)

    def scan(messages: list[str]) -> tuple[Counter, int]:
        hits, clean = Counter(), 0
        for text in messages:
            kinds = {s["kind"] for s in find_mechanical_signals(str(text))}
            if kinds:
                hits.update(kinds)
            else:
                clean += 1
        return hits, clean

    t0 = time.monotonic()
    spam_hits, spam_clean = scan(spam)
    ham_hits, ham_clean = scan(ham)
    seconds = time.monotonic() - t0

    print(f"{'детектор':18} {'на спаме':>10} {'на обычных':>12} {'во сколько раз чаще':>22}")
    print("-" * 66)
    kinds = sorted(set(spam_hits) | set(ham_hits),
                   key=lambda k: -(spam_hits[k] / max(len(spam), 1)))
    for kind in kinds:
        s = spam_hits[kind] / len(spam)
        h = ham_hits[kind] / len(ham)
        ratio = f"{s / h:.1f}x" if h > 0 else ("—" if s == 0 else "только на спаме")
        print(f"{kind:18} {s:9.1%} {h:11.1%} {ratio:>22}")

    print("-" * 66)
    print(f"{'без признаков':18} {spam_clean/len(spam):9.1%} {ham_clean/len(ham):11.1%}")
    print(f"\nпросмотрено {len(spam)+len(ham):,} сообщений за {seconds:.1f} с "
          f"({(len(spam)+len(ham))/seconds:,.0f} сообщений в секунду), "
          f"моделей не вызывали ни разу")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
