#!/usr/bin/env python3
"""Состояние качества одной командой: где мы и что мешает.

    python3 scripts/quality.py

Печатает то, что спросит жюри: сколько примеров и откуда, что показывает
прогон, какую долю ошибок мы вправе обещать и чего не хватает до следующего
обещания. Всё из файлов, ничего не придумывает.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.calibrate import clopper_pearson_upper  # noqa: E402

CASES = ROOT / "evals" / "cases_authenticity.jsonl"
REPORT = ROOT / "evals" / "report.json"
REAL_DIR = ROOT / "evals" / "real"


def main() -> int:
    if not CASES.exists():
        print("нет набора примеров")
        return 1
    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    sources = Counter(c.get("source", "—") for c in cases)
    difficulty = Counter(c.get("difficulty", "—") for c in cases)
    real = sum(n for s, n in sources.items() if s.startswith("real"))

    print("РАЗМЕЧЕННЫЕ ПРИМЕРЫ")
    print(f"  всего: {len(cases)}   настоящих: {real}   написанных нами: {len(cases) - real}")
    print(f"  по сложности: {dict(difficulty)}")

    pending = []
    if REAL_DIR.is_dir():
        for path in sorted(REAL_DIR.glob("*.jsonl")):
            rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            ids = {json.loads(l).get("id") for l in rows}
            merged = {c.get("id") for c in cases}
            left = len(ids - merged)
            if left:
                pending.append((path.name, left))
    if pending:
        print("  прислано, но ещё не размечено и не влито:")
        for name, n in pending:
            print(f"      {name}: {n}")

    print("\nЧТО ПОКАЗЫВАЕТ ПРОГОН")
    if REPORT.exists():
        rep = json.loads(REPORT.read_text(encoding="utf-8"))
        print(f"  бэкенд: {rep.get('backend')}   кейсов в прогоне: {rep.get('cases')}")
        for arm in rep.get("arms", []):
            print(f"  {arm['arm']:9} верных там где ответил: {arm['accuracy_on_answered']:.1%}"
                  f"   неверных выдано: {arm['wrong_delivered']}"
                  f"   покрытие: {arm['coverage']:.1%}"
                  f"   ${arm['cost_per_case_usd']:.5f}/кейс")
        per = rep.get("per_case", [])
        by_source = {c["id"]: c.get("source", "—") for c in cases}
        for arm in ("cascade", "baseline"):
            buckets: dict[str, list[bool]] = {}
            for row in per:
                cell = row.get(arm)
                if not cell:
                    continue
                key = "настоящие" if by_source.get(row["id"], "").startswith("real") else "наши"
                buckets.setdefault(key, []).append(bool(cell.get("correct")))
            if buckets:
                parts = [f"{k}: {sum(v)}/{len(v)}" for k, v in sorted(buckets.items())]
                print(f"  {arm:9} верных по источникам — {', '.join(parts)}")
    else:
        print("  отчёта нет, прогоните python3 -m evals.run")

    print("\nЧТО МЫ ВПРАВЕ ОБЕЩАТЬ ЖЮРИ")
    # Половина примеров уходит в калибровку, половина в проверку.
    calib = len(cases) // 2
    best = None
    # Снизу вверх: нас интересует САМОЕ сильное обещание, которое мы вправе
    # дать. Перебор сверху останавливался на первом же и занижал результат.
    for alpha in (0.03, 0.05, 0.10, 0.15, 0.20, 0.30):
        if clopper_pearson_upper(0, calib) <= alpha:
            best = alpha
            break
    if best:
        print(f"  при {calib} калибровочных примерах и нуле ошибок — "
              f"«не больше {best:.0%} неверных среди выданных ответов»")
    else:
        print(f"  при {calib} калибровочных примерах обещать нечего: "
              f"слишком мало данных")
    for alpha in (0.10, 0.05):
        need = next(k for k in range(1, 500) if clopper_pearson_upper(0, k) <= alpha)
        print(f"  для «не больше {alpha:.0%}» нужно {need} калибровочных,"
              f" то есть примерно {need * 2} примеров всего"
              f" — не хватает {max(0, need * 2 - len(cases))}")

    print("\nЧТО ПРОВЕРЯЕТСЯ АВТОМАТИЧЕСКИ")
    print("  python3 -m evals.run          метрики на наборе")
    print("  python3 -m core.verify        детекторы и механические барьеры")
    print("  python3 scripts/route_stats.py  маршрутизатор не выродился")
    print("  bash scripts/smoke.sh         всё вместе, гоняется перед каждым пушем")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
