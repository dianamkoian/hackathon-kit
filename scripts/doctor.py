#!/usr/bin/env python3
"""Проверка окружения. Работает и на macOS, и на Windows, и на Linux.

    python scripts/doctor.py           # что есть, чего нет
    python scripts/doctor.py --setup   # включить проверки перед пушем

На Windows команды `python3` обычно нет — пишите `python`. Всё остальное
одинаково: ядро проекта держится на стандартной библиотеке и работает везде.
"""
from __future__ import annotations

import argparse
import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK, NO, WARN = "  ok   ", "  нет  ", "  !    "


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def git(*args: str) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return done.stdout.strip()
    except FileNotFoundError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setup", action="store_true",
                        help="включить проверки перед пушем")
    args = parser.parse_args()

    system = platform.system()
    apple_silicon = system == "Darwin" and platform.machine() == "arm64"
    problems = 0

    print(f"система: {system} {platform.machine()}")
    print(f"python:  {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        print(f"{WARN}нужен Python 3.10 или новее — этот не подойдёт")
        problems += 1
    print()

    print("что нужно обязательно:")
    print(f"{OK if has('fastapi') else NO}fastapi   — демо-страница и API")
    print(f"{OK if has('uvicorn') else NO}uvicorn   — запуск сервера")
    if not (has("fastapi") and has("uvicorn")):
        print("       поставить:  pip install -r requirements.txt")
        problems += 1
    print()

    print("модели — хотя бы один вариант должен быть доступен:")
    ways = []
    if has("anthropic"):
        ways.append("anthropic")
        print(f"{OK}LLM_BACKEND=anthropic  — платная модель по ключу, работает везде")
    else:
        print(f"{NO}LLM_BACKEND=anthropic  — pip install anthropic, нужен ключ в .env")

    if apple_silicon and has("mlx_lm"):
        ways.append("mlx")
        print(f"{OK}LLM_BACKEND=mlx        — локальные модели на видеоядре Apple")
    elif apple_silicon:
        print(f"{NO}LLM_BACKEND=mlx        — pip install mlx-lm")
    else:
        print(f"{WARN}LLM_BACKEND=mlx        — только на Mac с процессором Apple, здесь недоступен")

    if shutil.which("ollama"):
        ways.append("ollama")
        print(f"{OK}ollama                 — локальные модели на Windows и Linux")
    else:
        print(f"{WARN}ollama                 — не установлен (ollama.com), это путь для Windows")

    print(f"{OK}LLM_BACKEND=mock       — заглушки: без ключа и без сети, работает везде")
    if not ways:
        print("       настоящих моделей нет, но на заглушках проект запускается полностью")
    print()

    print("для записи демо:")
    ffmpeg = shutil.which("ffmpeg") or (
        __import__("imageio_ffmpeg").get_ffmpeg_exe() if has("imageio_ffmpeg") else None
    )
    print(f"{OK if ffmpeg else NO}ffmpeg    {'— ' + str(ffmpeg) if ffmpeg else '— pip install imageio-ffmpeg'}")
    if system == "Darwin":
        print("       запись экрана: Cmd+Shift+5")
    elif system == "Windows":
        print("       запись экрана: Win+Alt+R (игровая панель) или любая другая программа")
    print()

    print("git:")
    name, email = git("config", "user.name"), git("config", "user.email")
    if email:
        print(f"{OK}коммиты подписываются как {name} <{email}>")
    else:
        print(f"{NO}имя для коммитов не задано:")
        print('       git config user.email ты@example.com')
        print('       git config user.name "Твоё Имя"')
        problems += 1

    hooks = git("config", "core.hooksPath")
    if hooks == ".githooks":
        print(f"{OK}проверки перед пушем включены")
    elif args.setup:
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=ROOT)
        print(f"{OK}проверки перед пушем включены")
    else:
        print(f"{WARN}проверки перед пушем выключены — включить: python scripts/doctor.py --setup")

    print()
    if problems:
        print(f"нерешённых проблем: {problems}. Их стоит закрыть до начала.")
        return 1
    print("окружение готово. Проверить целиком: python -m evals.run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
