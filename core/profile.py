"""Личный контекст и один уточняющий вопрос вместо догадки.

Тема спрашивает, как действовать при неопределённости. Обычный ответ — выдать
вероятность. Наш ответ другой: **задать один вопрос, ответ на который меняет
вывод**, и задать его человеку, который единственный его знает.

Пример. «Ваша посылка задерживается, перейдите по ссылке» — само по себе это
и обман, и обычное уведомление одновременно. Ссылка видна, но смысла она не
решает. Зато один вопрос решает: ждёте ли вы сейчас посылку? Эти сведения
есть только у человека, и спросить дешевле, чем угадать.

Личный контекст — то же самое, но заранее: кто ваши близкие и как они обычно
пишут, где вы работаете, какими сервисами пользуетесь. Тогда часть вопросов
отпадает, а часть ответов становится точнее: «мама с нового номера» значит
одно, если у мамы есть ваш постоянный номер в контактах, и другое, если нет.

Хранится только то, что человек сам ввёл, и только для подсказок. Никакие
данные никуда не уходят: всё остаётся в файле на машине, где крутится сервис.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Person:
    name: str                      # «мама», «Анна»
    relation: str = ""             # «мать», «коллега»
    usual_contact: str = ""        # «звонит с одного номера», «только в телеграме»


@dataclass
class Profile:
    """То, что человек рассказал о себе. Всё поля необязательные."""

    people: list[Person] = field(default_factory=list)
    employer: str = ""
    bank: str = ""
    services: list[str] = field(default_factory=list)   # «СДЭК», «Ozon»
    expecting: list[str] = field(default_factory=list)  # «посылка», «выплата»

    def knows(self, name: str) -> Person | None:
        needle = name.strip().casefold()
        return next((p for p in self.people if p.name.casefold() == needle), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Profile":
        return cls(
            people=[Person(**p) for p in raw.get("people", []) if p.get("name")],
            employer=raw.get("employer", ""),
            bank=raw.get("bank", ""),
            services=list(raw.get("services", [])),
            expecting=list(raw.get("expecting", [])),
        )


@dataclass
class Question:
    """Уточнение, ответ на которое меняет вывод."""

    text: str
    if_yes: str          # что это означает
    if_no: str
    raises_doubt: str    # какой ответ ухудшает оценку: "yes" или "no"

    def to_dict(self) -> dict:
        return asdict(self)


# Правила заданы явно и их мало. Каждое звучит так: «такой признак + такое
# незнание = такой вопрос». Угадывание начинается там, где правил нет, и
# поэтому там мы просто молчим.
def clarifying_question(markers: list[dict] | None, text: str,
                        profile: Profile | None) -> Question | None:
    kinds = {m.get("type") for m in (markers or []) if isinstance(m, dict)}
    low = text.casefold()
    profile = profile or Profile()

    if "external_link" in kinds and any(
            w in low for w in ("посылк", "доставк", "заказ", "трек", "отправлен")):
        if "посылка" not in [e.casefold() for e in profile.expecting]:
            return Question(
                text="Вы сейчас ждёте посылку?",
                if_yes="Тогда откройте приложение службы доставки сами и найдите заказ там — "
                       "настоящее уведомление будет и в приложении.",
                if_no="Тогда это почти наверняка обман: уведомление о доставке приходит "
                      "тем, кто что-то заказывал.",
                raises_doubt="no")

    if kinds & {"new_contact", "money_request"}:
        known = next((p for p in profile.people
                      if p.name.casefold() in low), None)
        if known:
            return Question(
                text=f"Вы можете позвонить «{known.name}» на номер из своих контактов прямо сейчас?",
                if_yes="Позвоните. Это снимает вопрос за тридцать секунд.",
                if_no="Тогда спросите то, что знает только этот человек, — "
                      "голос подделывается, общая история нет.",
                raises_doubt="no")
        return Question(
            text="Этот человек есть у вас в контактах со старым номером?",
            if_yes="Позвоните ему на старый номер. Мошенник контролирует новый, но не старый.",
            if_no="Тогда вы не можете подтвердить, кто это. Не переводите деньги.",
            raises_doubt="no")

    if "authority" in kinds and any(w in low for w in ("банк", "счёт", "счет", "карт")):
        if profile.bank:
            return Question(
                text=f"Это пришло от «{profile.bank}» — вашего банка?",
                if_yes="Откройте приложение банка сами. Настоящее уведомление будет там.",
                if_no="Чужой банк пишет вам о вашем счёте — это не бывает по ошибке.",
                raises_doubt="no")
        return Question(
            text="У вас есть счёт в этом банке?",
            if_yes="Откройте приложение банка сами, не по ссылке.",
            if_no="Банк, в котором у вас нет счёта, не может писать вам о блокировке.",
            raises_doubt="no")

    if kinds & {"too_good", "prepayment"}:
        return Question(
            text="Вы участвовали в этом розыгрыше или откликались на это предложение?",
            if_yes="Проверьте организатора на официальном сайте, а не по ссылке из письма.",
            if_no="Выигрыш в розыгрыше, в котором вы не участвовали, не бывает.",
            raises_doubt="no")

    return None


def missing_context(profile: Profile | None) -> list[str]:
    """Чего система о вас не знает — чтобы предложить дополнить, но не требовать."""
    profile = profile or Profile()
    gaps = []
    if not profile.people:
        gaps.append("кто ваши близкие и как они обычно пишут")
    if not profile.bank:
        gaps.append("каким банком вы пользуетесь")
    if not profile.services:
        gaps.append("какими сервисами доставки и маркетплейсами")
    if not profile.expecting:
        gaps.append("ждёте ли вы сейчас посылку или выплату")
    return gaps
