#!/usr/bin/env python3
"""Проверяет, что маршрутизатор действительно маршрутизирует.

Зачем отдельная проверка. В ВКР Дианы описан отказ, который легко повторить:
механизм выбора режима (ModeGate) выучился опираться на один доминирующий
признак, веса режимов выродились в [1,00; 0,00; 0,00], и конструкция из трёх
голов функционально свелась к одной. Точность при этом осталась приличной —
вырождение по ней не видно.

У нас ровно та же уязвимость: каскад осмыслен, только пока дешёвый уровень
реально берёт на себя часть работы. Мы это уже ловили: на модели 1.5B
эскалировали все примеры до одного, то есть платили за оба уровня и не
выигрывали ничего. По таблице точности это незаметно.

    python3 scripts/route_stats.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPORT = Path(__file__).resolve().parent.parent / "evals" / "report.json"


def main() -> int:
    if not REPORT.exists():
        print("нет evals/report.json — прогоните `python3 -m evals.run`")
        return 1

    data = json.loads(REPORT.read_text(encoding="utf-8"))
    per_case = data.get("per_case", [])
    if not per_case:
        print("в отчёте нет разбивки по кейсам")
        return 1

    cascade = [row["cascade"] for row in per_case if "cascade" in row]
    if not cascade:
        print("в отчёте нет прогона каскада (--arm cascade или both)")
        return 1

    statuses = Counter(c["status"] for c in cascade)
    tiers = Counter(c.get("tier_used") or "—" for c in cascade)
    total = len(cascade)

    print(f"кейсов: {total}   спека: {data.get('spec')}   бэкенд: {data.get('backend')}\n")
    print("чем закончилось:")
    for name, label in (("ok", "ответ с первого уровня"),
                        ("escalated", "ответ после эскалации"),
                        ("abstained", "отказ"),
                        ("error", "ошибка")):
        n = statuses.get(name, 0)
        if n:
            print(f"  {label:26} {n:3}  ({n / total:.0%})")

    print("\nкакой уровень дал ответ:")
    for tier, n in tiers.most_common():
        print(f"  {tier:26} {n:3}  ({n / total:.0%})")

    cheap_answered = statuses.get("ok", 0)
    share = cheap_answered / total if total else 0.0
    print()
    if share < 0.2:
        print(f"ВЫРОЖДЕНИЕ: дешёвый уровень закрыл только {share:.0%} примеров.")
        print("  Каскад платит за оба уровня и почти ничего не экономит.")
        print("  Либо дешёвая модель не тянет задачу, либо порог уверенности завышен,")
        print("  либо маршрутизатор отправляет на сильный уровень всё подряд.")
        return 1
    print(f"маршрутизация работает: дешёвый уровень закрыл {share:.0%} примеров")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
