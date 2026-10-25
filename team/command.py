#!/usr/bin/env python3
"""Команды между агентами: поставить и посмотреть.

    python3 team/command.py --to альбина --about "разметить примеры" \
            --why "без этого нет гарантии" --done "58 размеченных примеров"
    python3 team/command.py --list
    python3 team/command.py --list --to саша

Нумерация и формат — машиной, чтобы шесть агентов не изобретали шесть
форматов и не спорили за один файл.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "team" / "команды"

# Зона адресата: команда не должна выходить за неё.
ZONES = {
    "диана": "core/pipeline.py, core/providers.py, domains/<тема>.py, пороги",
    "альбина": "evals/",
    "софья": "core/verify.py, core/structured.py",
    "соня": "core/verify.py, core/structured.py",
    "карина": "README.md, docs/",
    "дима": "api/, деплой",
    "саша": "web/",
}


def slug(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")[:limit]


def next_number() -> int:
    numbers = [int(m.group(1)) for p in DIR.glob("*.md")
               if (m := re.match(r"^(\d+)-", p.name))]
    return max(numbers, default=0) + 1


def author() -> str:
    try:
        done = subprocess.run(["git", "config", "user.name"], cwd=ROOT,
                              capture_output=True, text=True)
        return done.stdout.strip() or "неизвестно"
    except FileNotFoundError:
        return "неизвестно"


def status_of(text: str) -> str:
    match = re.search(r"^\*\*Статус:\*\*\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else "?"


def addressee_of(text: str) -> str:
    match = re.search(r"^\*\*Кому:\*\*\s*([^\s(]+)", text, re.MULTILINE)
    return match.group(1).strip().lower() if match else ""


def cmd_list(to: str | None) -> int:
    files = sorted(p for p in DIR.glob("*.md") if not p.name.startswith("_")
                   and p.name != "README.md")
    shown = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        status = status_of(text)
        who = addressee_of(text)
        if to and who != to.lower():
            continue
        if status.startswith(("сделана", "отклонена")):
            continue
        title = text.splitlines()[0].lstrip("# ").strip()
        print(f"  [{status}] {path.name}")
        print(f"      кому: {who or '?'}   {title}")
        shown += 1
    if not shown:
        print("  открытых команд нет" + (f" для {to}" if to else ""))
    return 0


def cmd_create(to: str, about: str, why: str, done: str, what: str | None) -> int:
    key = to.lower()
    if key not in ZONES:
        print(f"не знаю такого адресата: {to}. Известные: {', '.join(sorted(ZONES))}")
        return 1

    number = next_number()
    path = DIR / f"{number:03d}-{key}-{slug(about)}.md"
    path.write_text(
        f"# {number:03d}. {about}\n\n"
        f"**От:** {author()}\n"
        f"**Кому:** {to} (зона: {ZONES[key]})\n"
        f"**Статус:** открыта\n\n"
        f"## Что сделать\n{what or about}\n\n"
        f"## Зачем\n{why}\n\n"
        f"## Как понять, что готово\n{done}\n\n"
        f"## Ответ\n⟨заполняет {to}: сделано / в работе / отклонено и почему⟩\n",
        encoding="utf-8",
    )
    print(f"создана: {path.relative_to(ROOT)}")
    print("не забудь: git add + commit + push, иначе адресат её не увидит")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="показать открытые команды")
    parser.add_argument("--to", help="кому")
    parser.add_argument("--about", help="что нужно, одной строкой")
    parser.add_argument("--what", help="подробнее, по шагам")
    parser.add_argument("--why", default="", help="зачем")
    parser.add_argument("--done", default="", help="как понять, что готово")
    args = parser.parse_args()

    DIR.mkdir(parents=True, exist_ok=True)
    if args.list:
        return cmd_list(args.to)
    if not (args.to and args.about):
        parser.print_help()
        return 1
    return cmd_create(args.to, args.about, args.why, args.done, args.what)


if __name__ == "__main__":
    raise SystemExit(main())
