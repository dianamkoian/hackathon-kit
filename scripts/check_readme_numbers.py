#!/usr/bin/env python3
"""Сверяет таблицу метрик в README с последним прогоном измерения.

Правило «числа только из прогона» нельзя оставлять на честное слово: под конец
кто-нибудь поправит цифру руками, чтобы выглядело лучше, и жюри попросит
воспроизвести. Поэтому сверяет машина.

    python3 scripts/check_readme_numbers.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rows(text: str) -> list[str]:
    """Строки таблицы метрик: те, что начинаются с названия варианта."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\|\s*(baseline|каскад)\s*\|", stripped):
            # Пробелы не считаем расхождением, всё остальное считаем.
            out.append(re.sub(r"\s+", " ", stripped))
    return out


def main() -> int:
    readme = ROOT / "README.md"
    report = ROOT / "evals" / "report.md"

    if not report.exists():
        print("нет evals/report.md — прогоните `python3 -m evals.run`")
        return 1

    in_readme = rows(readme.read_text(encoding="utf-8"))
    in_report = rows(report.read_text(encoding="utf-8"))

    if not in_readme:
        print("в README нет таблицы метрик — вставьте её из evals/report.md")
        return 1

    if in_readme == in_report:
        print(f"ok: числа в README совпадают с прогоном ({len(in_readme)} строки)")
        return 0

    print("РАСХОЖДЕНИЕ: таблица в README не совпадает с evals/report.md\n")
    print("  в README:")
    for line in in_readme:
        print(f"      {line}")
    print("  в прогоне:")
    for line in in_report:
        print(f"      {line}")
    print("\n  Вставьте таблицу из evals/report.md в README. Руками числа не правим:")
    print("  жюри попросит воспроизвести, и это будет провал защиты.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
