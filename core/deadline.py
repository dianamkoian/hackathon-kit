"""Модель отвечает в срок или не отвечает вовсе.

Замерено на локальной модели: один запрос — 20 секунд, три одновременных —
57 секунд каждому, потому что вычисление идёт по очереди. На защите это
означает, что жюри, кликнув втроём, ждёт минуту и видит крутилку.

Поэтому модель здесь не обязанность, а возможность. Ей даётся срок и один
рабочий поток. Не уложилась или занята — отвечаем тем, что видят детекторы,
и честно помечаем ответ быстрым. Человек всегда получает ответ за секунды;
вопрос только в том, насколько он подробный.

Это же и есть фолбэк на случай, когда модели нет совсем: «недоступна» —
это не только «не установлена», но и «занята» и «не успевает».
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable

DEADLINE_SECONDS = float(os.getenv("MODEL_DEADLINE", "8"))


@dataclass
class Attempt:
    """Чем кончилась попытка позвать модель."""

    value: Any = None
    used_model: bool = False
    reason: str = ""      # почему модель не использовалась

    @property
    def ok(self) -> bool:
        return self.used_model and self.value is not None


class DeadlineRunner:
    """Один рабочий поток и срок на ответ.

    Один поток, потому что модель всё равно считает по очереди: очередь из
    пяти запросов не ускоряет ни один из них, зато растит ожидание. Лучше
    честно сказать «занята» второму, чем заставить ждать обоих.
    """

    def __init__(self, deadline: float | None = None):
        self.deadline = DEADLINE_SECONDS if deadline is None else deadline
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")
        self._busy = threading.Event()
        self.stats = {"model": 0, "timeout": 0, "busy": 0, "error": 0}

    def run(self, fn: Callable[[], Any]) -> Attempt:
        if self._busy.is_set():
            self.stats["busy"] += 1
            return Attempt(reason="модель занята другим запросом")

        self._busy.set()

        def task():
            try:
                return fn()
            finally:
                # Освобождаем, даже если наверху уже сдались по сроку: иначе
                # один зависший вызов закрыл бы модель навсегда.
                self._busy.clear()

        future: Future = self._pool.submit(task)
        try:
            value = future.result(timeout=self.deadline)
        except FutureTimeout:
            self.stats["timeout"] += 1
            # Отменить вычисление нельзя — оно досчитает и освободит поток
            # само. Мы просто не ждём.
            return Attempt(reason=f"модель не уложилась в {self.deadline:.0f} с")
        except Exception as exc:  # noqa: BLE001
            self.stats["error"] += 1
            return Attempt(reason=f"модель упала: {type(exc).__name__}")
        self.stats["model"] += 1
        return Attempt(value=value, used_model=True)
