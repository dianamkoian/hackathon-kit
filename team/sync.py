#!/usr/bin/env python3
"""Синхронизация команды: раз в 15 минут одной командой.

    python3 team/sync.py              один раз
    python3 team/sync.py --watch      каждые 15 минут, пока не остановишь

Что делает:
  1. забирает чужие изменения (git pull --rebase);
  2. отправляет ТОЛЬКО твои файлы из team/ — статус, решения, вопросы;
  3. печатает сводку: кто чем занят, кто застрял, какие файлы не трогать.

Почему только team/, а не весь код: автоматически отправлять недописанный код
каждые 15 минут — верный способ сломать работу остальным. Код коммитишь сама,
когда он работает. Эта команда синхронизирует договорённости, а не правки.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAM = ROOT / "team"
INTERVAL = 15 * 60


def git(*args: str, check: bool = True) -> tuple[int, str]:
    done = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
    )
    out = (done.stdout + done.stderr).strip()
    if check and done.returncode != 0:
        raise RuntimeError(out)
    return done.returncode, out


def section(text: str, name: str) -> str:
    """Достаёт содержимое раздела `## name` из файла статуса."""
    match = re.search(rf"^##\s*{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)",
                      text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    body = match.group(1).strip()
    # Незаполненный шаблон не показываем как содержательный ответ.
    return "" if body.startswith("⟨") else body


def pull() -> str:
    """Забирает чужое. --autostash, чтобы недописанный код не мешал."""
    code, out = git("pull", "--rebase", "--autostash", check=False)
    if code == 0:
        return "забрала чужие изменения"
    if "CONFLICT" in out or "could not apply" in out:
        git("rebase", "--abort", check=False)
        return ("КОНФЛИКТ при обновлении, откатила. Разберись вручную: "
                "скорее всего двое правили один файл кода.")
    return f"не вышло забрать: {out.splitlines()[-1] if out else 'нет связи'}"


def push_team() -> str:
    """Отправляет только изменения внутри team/."""
    _, changed = git("status", "--porcelain", "--", "team")
    if changed.strip():
        git("add", "--", "team")
        git("commit", "-q", "-m", "team: обновление статуса")
        message = "отправила твои изменения в team/"
    else:
        message = "в team/ у тебя нечего отправлять"

    code, out = git("push", "-q", "origin", "HEAD", check=False)
    if code != 0:
        return f"{message}, но push не прошёл: {out.splitlines()[-1] if out else 'нет связи'}"
    return message


def digest() -> list[str]:
    lines: list[str] = []
    statuses = sorted(p for p in (TEAM / "статусы").glob("*.md")
                      if not p.name.startswith("_"))
    if not statuses:
        lines.append("  статусов пока нет — скопируй team/статусы/_ШАБЛОН.md")

    for path in statuses:
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        now = section(text, "Сейчас делаю") or "—"
        stuck = section(text, "Застрял(а) на")
        locked = section(text, "Не трогайте")
        lines.append(f"  {title}")
        lines.append(f"      сейчас: {now.splitlines()[0][:90]}")
        if stuck:
            lines.append(f"      ЗАСТРЯЛ: {stuck.splitlines()[0][:90]}")
        if locked:
            lines.append(f"      не трогать: {locked.splitlines()[0][:90]}")

    open_questions = []
    for path in sorted((TEAM / "вопросы").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "Статус:** открыт" in text or "Статус: открыт" in text:
            open_questions.append(path.stem)
    if open_questions:
        lines.append("  открытые вопросы: " + ", ".join(open_questions))

    return lines


def once() -> None:
    print(f"\n=== синхронизация {time.strftime('%H:%M')}")
    print(f"  {pull()}")
    print(f"  {push_team()}")

    _, recent = git("log", "--oneline", "-5", "--format=%h %an: %s")
    print("\n  последние коммиты:")
    for line in recent.splitlines():
        print(f"      {line[:100]}")

    print("\n  состояние команды:")
    for line in digest():
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", action="store_true",
                        help="повторять каждые 15 минут, пока не остановишь (Ctrl+C)")
    parser.add_argument("--interval", type=int, default=INTERVAL,
                        help="другой интервал в секундах")
    args = parser.parse_args()

    try:
        once()
        while args.watch:
            time.sleep(args.interval)
            once()
    except KeyboardInterrupt:
        print("\nостановлено")
    except RuntimeError as exc:
        print(f"\nошибка git: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
