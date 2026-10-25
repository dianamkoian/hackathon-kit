"""Свободное описание ситуации — подбор проверки по смыслу.

Второй режим продукта, и у него другая природа, чем у разбора сообщения.

Когда человек вставляет само сообщение, приёмы манипуляции лежат в тексте, и
мы обязаны показать их дословными цитатами. Когда человек описывает ситуацию
своими словами — «меня добавили в чат по ЖКХ», «увидел в соцсетях объявление» —
цитировать нечего: в его словах нет обмана, в них есть рассказ о нём.

Поэтому здесь не разбор, а сопоставление: описание сравнивается по смыслу с
полем «сомнение» каждой проверки из каталога, и человек получает ту проверку,
которая снимает именно его сомнение.

Модель здесь — эмбеддинги, а не генерация. Она ничего не пишет, поэтому не
может выдумать: всё, что увидит человек, взято из каталога, написанного людьми.

    python3 -m core.situations "меня добавили в чат дома по ЖКХ"
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CHECKS_PATH = Path(__file__).resolve().parent.parent / "domains" / "checks.json"
MODEL_NAME = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-base")

# Ниже этого сходства считаем, что каталог не про это. Лучше честно сказать
# «не нашли подходящей проверки», чем выдать ближайшую и сбить человека.
MIN_SIMILARITY = float(os.getenv("SITUATION_MIN_SIM", "0.78"))


@dataclass
class Suggestion:
    check: dict
    similarity: float

    def to_dict(self) -> dict:
        return {"check": self.check, "similarity": round(self.similarity, 3)}


class SituationMatcher:
    """Сопоставляет описание ситуации с каталогом проверок."""

    def __init__(self, model_name: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.checks = [c for c in json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
                       if c["id"] != "C00"]
        self._model = SentenceTransformer(model_name or MODEL_NAME)
        # e5 требует префиксов: "passage:" для того, что ищем, "query:" для запроса.
        # Без них качество заметно падает — это не украшение, а часть модели.
        texts = [f"passage: {c['doubt']}" for c in self.checks]
        self._vectors = self._model.encode(texts, normalize_embeddings=True)

    def suggest(self, situation: str, top: int = 3) -> list[Suggestion]:
        if not situation.strip():
            return []
        query = self._model.encode([f"query: {situation}"], normalize_embeddings=True)
        # Векторы нормированы, поэтому скалярное произведение и есть косинус.
        scores = (self._vectors @ query[0])
        order = scores.argsort()[::-1][:top]
        return [Suggestion(self.checks[i], float(scores[i])) for i in order
                if float(scores[i]) >= MIN_SIMILARITY]


_matcher: SituationMatcher | None = None


def matcher() -> SituationMatcher:
    """Модель грузится один раз на процесс: загрузка дороже самого поиска."""
    global _matcher
    if _matcher is None:
        _matcher = SituationMatcher()
    return _matcher


if __name__ == "__main__":
    import sys
    import time

    question = " ".join(sys.argv[1:]) or "меня добавили в чат дома по ЖКХ"
    t0 = time.monotonic()
    m = matcher()
    loaded = time.monotonic() - t0
    t0 = time.monotonic()
    found = m.suggest(question)
    print(f"модель загружена за {loaded:.1f} с, поиск за {(time.monotonic()-t0)*1000:.0f} мс\n")
    print(f"ситуация: {question}\n")
    if not found:
        print("подходящей проверки в каталоге нет — так и скажем человеку")
    for s in found:
        print(f"  [{s.similarity:.2f}] {s.check['action']}")
        print(f"         почему: {s.check['why']}")
