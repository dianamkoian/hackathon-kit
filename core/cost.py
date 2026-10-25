"""Цена ошибки несимметрична — значит и порог не должен быть симметричным.

Это главный довод продукта, и он считается, а не декларируется.

Две ошибки стоят разного. Если мы сказали «сомневаюсь» на настоящем письме,
человек теряет тридцать секунд на звонок. Если мы сказали «верьте» на
мошенническом — человек теряет деньги. Отношение порядка десяти тысяч к одному.

Отсюда следствие, которое меняет постановку задачи: **оптимизировать точность
бессмысленно**. Точность считает обе ошибки одинаковыми, а они отличаются
в тысячи раз. Оптимизировать надо ожидаемый убыток человека.

Метод — cost-sensitive thresholding (Sheng & Ling), перенос из финтех-домена,
где ровно так работают с дисбалансом: там пропустить мошенническую транзакцию
дороже, чем задержать честную.

    python3 -m core.cost
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Сколько стоит человеку каждая ошибка. Значения грубые и названы явно, чтобы
# на защите их можно было оспорить по существу, а не угадывать.
LOSS_MISSED_SCAM = float(os.getenv("LOSS_MISSED_SCAM", "18000"))   # рублей: средний перевод мошеннику
LOSS_FALSE_ALARM = float(os.getenv("LOSS_FALSE_ALARM", "2.5"))     # рублей: 30 секунд времени
SECONDS_TO_CHECK = 30
HOURLY_RATE = 300.0  # рублей в час — отсюда и берётся 2.5 рубля за полминуты


@dataclass
class Outcome:
    threshold: float
    answered: int
    missed_scams: int       # сказали «верьте», а был обман
    false_alarms: int       # сказали «сомневаюсь», а было честное письмо
    abstained: int
    total: int

    @property
    def expected_loss(self) -> float:
        """Ожидаемый убыток человека на одно сообщение.

        Отказ не бесплатен: человек всё равно делает проверку, то есть тратит
        те же тридцать секунд. Но он не теряет деньги — и в этом весь смысл.
        """
        loss = (self.missed_scams * LOSS_MISSED_SCAM
                + self.false_alarms * LOSS_FALSE_ALARM
                + self.abstained * LOSS_FALSE_ALARM)
        return loss / self.total if self.total else 0.0

    def report(self) -> str:
        return (f"порог {self.threshold:.2f}: "
                f"пропущено обманов {self.missed_scams}, "
                f"ложных тревог {self.false_alarms}, "
                f"отказов {self.abstained} — "
                f"убыток {self.expected_loss:,.0f} ₽ на сообщение")


def ratio() -> float:
    """Во сколько раз пропущенный обман дороже ложной тревоги."""
    return LOSS_MISSED_SCAM / LOSS_FALSE_ALARM


def expected_loss(*, missed: int, false_alarms: int, abstained: int, total: int) -> float:
    return Outcome(0.0, total - abstained, missed, false_alarms, abstained, total).expected_loss


def better_to_doubt() -> str:
    """Словами то, что следует из чисел — для слайда и для защиты."""
    k = ratio()
    return (
        f"Пропустить обман дороже ложной тревоги в {k:,.0f} раз "
        f"({LOSS_MISSED_SCAM:,.0f} ₽ против {LOSS_FALSE_ALARM:.1f} ₽ за "
        f"{SECONDS_TO_CHECK} секунд). Значит система обязана сомневаться "
        f"чаще, чем ошибаться в сторону доверия: один пропущенный обман "
        f"обесценивает {k:,.0f} правильных «всё в порядке»."
    )


if __name__ == "__main__":
    print(better_to_doubt())
    print()
    # Что это даёт на наших тридцати пяти примерах.
    scenarios = [
        ("всегда отвечаем, без отказов", dict(missed=2, false_alarms=0, abstained=0, total=35)),
        ("наш каскад с отказами",        dict(missed=0, false_alarms=0, abstained=2, total=35)),
        ("перестраховка: сомневаемся во всём", dict(missed=0, false_alarms=33, abstained=0, total=35)),
    ]
    print(f"{'сценарий':38} {'убыток на сообщение':>22}")
    print("-" * 62)
    for name, kw in scenarios:
        print(f"{name:38} {expected_loss(**kw):>19,.0f} ₽")
    print()
    print("Вывод: отказ дешевле ошибки почти в тысячу раз, а перестраховка")
    print("дешевле пропуска — но дороже, чем умение молчать по делу.")
