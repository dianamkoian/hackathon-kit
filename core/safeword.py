"""Контрольный вопрос: как сделать подделку бесполезной, а не ловить её.

Главная мысль продукта, и она идёт против хода всей индустрии.

Все строят **детекторы** подделок: отличить синтезированный голос от живого,
сгенерированное фото от снятого. Это гонка, в которой защита всегда на шаг
позади: каждое поколение генераторов обесценивает предыдущее поколение
детекторов. Выиграть в ней нельзя — можно только не отставать.

Мы предлагаем выйти из гонки. Дипфейк умеет скопировать **голос**, но не умеет
знать **общую историю**. Если вы с мамой заранее договорились о вопросе, ответ
на который есть только у вас двоих, подделка перестаёт работать — независимо
от того, насколько она хороша. Качество синтеза больше не имеет значения.

Это и есть ответ на вопрос темы «на чём строить доверие»: доверие строится не
на том, как звучит голос, а на том, что знают только двое.

Техническая часть здесь намеренно крошечная — вся ценность в постановке.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Приёмы, при которых собеседник выдаёт себя за кого-то: именно тогда уместно
# напомнить про контрольный вопрос. При ссылке на оплату он бесполезен.
IMPERSONATION_MARKERS = ("new_contact", "authority", "money_request", "secrecy")

_WS = re.compile(r"\s+")


def normalize_answer(answer: str) -> str:
    """Ответ сверяется без учёта регистра и пробелов: человек в панике печатает плохо."""
    return _WS.sub(" ", answer).strip().casefold()


def answer_fingerprint(answer: str) -> str:
    """Храним отпечаток, а не ответ.

    Контрольный ответ — это семейная тайна, и хранить её в открытом виде
    незачем: нам нужно только уметь сказать «совпало» или «нет».
    """
    return hashlib.sha256(normalize_answer(answer).encode("utf-8")).hexdigest()[:32]


@dataclass
class SafeWord:
    person: str          # «мама», «Анна»
    question: str        # «Как звали нашу первую собаку?»
    fingerprint: str     # отпечаток ответа

    @classmethod
    def create(cls, person: str, question: str, answer: str) -> "SafeWord":
        if not question.strip() or not answer.strip():
            raise ValueError("нужны и вопрос, и ответ")
        return cls(person.strip(), question.strip(), answer_fingerprint(answer))

    def matches(self, said: str) -> bool:
        return answer_fingerprint(said) == self.fingerprint

    def to_dict(self) -> dict:
        return {"person": self.person, "question": self.question}


def should_ask(markers: list[dict] | None) -> bool:
    """Нужно ли напоминать про контрольный вопрос для этого разбора."""
    kinds = {m.get("type") for m in (markers or []) if isinstance(m, dict)}
    return bool(kinds & set(IMPERSONATION_MARKERS))


def prompt_for(safeword: SafeWord | None, markers: list[dict] | None) -> dict | None:
    """Что показать человеку рядом с разбором."""
    if not should_ask(markers):
        return None
    if safeword is None:
        return {
            "state": "not_set",
            "title": "Договоритесь о контрольном вопросе",
            "text": ("Здесь собеседник выдаёт себя за близкого или за организацию. "
                     "Голос и стиль подделываются, общая история — нет. "
                     "Придумайте с близким вопрос, ответ на который знаете только вы двое, "
                     "и задайте его в такой момент."),
        }
    return {
        "state": "ready",
        "title": f"Спросите у «{safeword.person}»",
        "question": safeword.question,
        "text": ("Настоящий человек ответит сразу. Подделка не знает вашей общей "
                 "истории — и качество синтеза тут уже не поможет."),
    }
