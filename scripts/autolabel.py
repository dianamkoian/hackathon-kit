#!/usr/bin/env python3
"""Автоматическая разметка корпуса — с измеренным согласием с ручной.

Размеченных вручную примеров не бывает тысяч: каждый надо прочитать. Но тысячи
нужны, чтобы мерить на объёме. Выход — разметить сотню руками, построить
правила, **измерить, насколько правила совпадают с человеком**, и дальше
масштабировать, называя эту цифру вслух.

Без измерения автоматическая разметка — это самообман: система оценивалась бы
по собственным правилам. С измерением это обычная практика, у которой названы
границы.

Правила заданы явно и опираются на две вещи: метку корпуса (спам или нет) и
наблюдаемые признаки из детекторов. Ничего не угадывается.

    python3 scripts/autolabel.py --check      # согласие с ручной разметкой
    python3 scripts/autolabel.py --build 1000 # собрать набор
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.verify import find_mechanical_signals  # noqa: E402
from domains.authenticity import _SIGNAL_TO_MARKER  # noqa: E402

CASES = ROOT / "evals" / "cases_authenticity.jsonl"
OUT = ROOT / "evals" / "cases_auto.jsonl"
PARQUET = ("/Users/diana/.cache/huggingface/hub/datasets--benzlokzik--russian-spam-fork"
           "/snapshots/*/processed_combined.parquet")

# Слова, по которым узнаётся схема с работой: это самая частая категория в
# корпусе, и проверка для неё другая, чем для фишинга.
JOB_WORDS = re.compile(
    r"(?:ваканс|подработк|заработок|зарабатыва|работа\s+на\s+дому|удал[её]нн|"
    r"требуются|требуется|ищем\s+людей|набор\s+сотрудник|доход\s+от|оплата\s+от)",
    re.IGNORECASE)
MONEY_PROMISE = re.compile(r"(?:от|до)\s*\d[\d\s]{2,}\s*(?:р|руб|₽|тыс|к\b|\$)", re.IGNORECASE)


def label(text: str, is_spam: bool) -> dict:
    """Разметка одного сообщения по метке корпуса и наблюдаемым признакам."""
    signals = find_mechanical_signals(text)
    kinds = {s["kind"] for s in signals}
    markers = [{"type": _SIGNAL_TO_MARKER[s["kind"]], "quote": s["quote"]}
               for s in signals if s["kind"] in _SIGNAL_TO_MARKER]

    job = bool(JOB_WORDS.search(text))
    if job and MONEY_PROMISE.search(text):
        quote = MONEY_PROMISE.search(text).group(0).strip()
        markers.append({"type": "too_good", "quote": quote})

    # Оценка: метка корпуса говорит, спам это или нет; признаки говорят,
    # насколько уверенно. Спам без единого признака — честное «сомневаюсь»,
    # а не «обман»: мы не видим, за что его таким считать.
    if is_spam:
        assessment = "distrust" if (markers or job) else "doubt"
    else:
        assessment = "doubt" if markers else "trust"

    if "secret_request" in kinds:
        check = "C11"
    elif "card_number" in kinds:
        check = "C01"
    elif "mixed_script" in kinds:
        check = "C18"
    elif job:
        check = "C19"
    elif "link" in kinds:
        check = "C04"
    else:
        check = "C00"

    return {"markers": markers, "assessment": assessment, "check_id": check,
            "explanation": "размечено автоматически по метке корпуса и детекторам"}


def check_agreement() -> int:
    """Сравнивает правила с ручной разметкой — ту же самую, что делал человек."""
    manual = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines()
              if l.strip() and '"real-corpus"' in l]
    if not manual:
        print("нет ручной разметки корпуса для сравнения")
        return 1

    agree = same_check = 0
    confusion: dict[tuple[str, str], int] = {}
    for row in manual:
        want = row["expected"]
        # Метку корпуса восстанавливаем из ручной оценки: trust = не спам.
        is_spam = want["assessment"] != "trust"
        got = label(row["text"], is_spam)
        pair = (want["assessment"], got["assessment"])
        confusion[pair] = confusion.get(pair, 0) + 1
        agree += want["assessment"] == got["assessment"]
        same_check += want["check_id"] == got["check_id"]

    n = len(manual)
    print(f"сравнение с ручной разметкой на {n} примерах:")
    print(f"  оценка совпала:   {agree}/{n} = {agree/n:.0%}")
    print(f"  проверка совпала: {same_check}/{n} = {same_check/n:.0%}")
    print("\n  где расходимся (человек -> правила):")
    for (want, got), k in sorted(confusion.items(), key=lambda kv: -kv[1]):
        mark = "  " if want == got else "≠ "
        print(f"    {mark}{want:9} -> {got:9} {k}")
    return 0


def build(n: int, seed: int = 23) -> int:
    import pandas as pd

    files = glob.glob(PARQUET)
    if not files:
        print("корпус не скачан")
        return 1
    df = pd.read_parquet(files[0])
    half = n // 2
    rows = []
    for is_spam, want in ((True, half), (False, n - half)):
        pool = df[df["label"] == is_spam].sample(want * 3, random_state=seed)["text"]
        seen = set()
        for text in pool:
            text = re.sub(r"\s+", " ", str(text)).strip()
            if not (40 <= len(text) <= 500) or text[:30] in seen:
                continue
            seen.add(text[:30])
            rows.append({
                "id": f"a-{'s' if is_spam else 'h'}{len(rows):04d}",
                "difficulty": "auto", "source": "auto-corpus", "text": text,
                "expected": label(text, is_spam),
                "mock": {"cheap": "correct", "strong": "correct"},
            })
            if sum(1 for r in rows if r["id"].startswith(f"a-{'s' if is_spam else 'h'}")) >= want:
                break
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"собрано {len(rows)} автоматически размеченных примеров -> {OUT}")
    print(f"размер файла: {OUT.stat().st_size/1024:.0f} КБ")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--build", type=int, default=0)
    args = ap.parse_args()
    if args.build:
        return build(args.build)
    return check_agreement()


if __name__ == "__main__":
    raise SystemExit(main())
