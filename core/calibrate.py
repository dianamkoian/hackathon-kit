"""Калибровка порога отказа с гарантией на долю ошибок.

Зачем. Порог «отвечаем, если уверенность ≥ 0,6» взят с потолка. На защите на
вопрос «почему 0,6» отвечать нечем. Конформная калибровка (перенос из § 3.4
ВКР, каскад с conformal-prediction-калибратором) заменяет его на порог,
подобранный под заданную долю ошибок: «не больше 5% неверных среди выданных
ответов».

Метод (split conformal). Примеры делятся на калибровочные и проверочные. На
калибровочных перебираем пороги и для каждого считаем верхнюю границу доли
ошибок среди выданных ответов. Берём наименьший порог, у которого граница не
превышает заданного уровня, — наименьший, потому что он даёт максимальное
покрытие при соблюдении гарантии. Затем проверяем на отложенной выборке,
которая в подборе не участвовала.

Почему именно верхняя граница, а не сама доля. На 30 примерах доля 0/6 = 0
ничего не гарантирует: шесть наблюдений просто не могут отличить нулевую
ошибку от пятипроцентной. Граница Клоппера — Пирсона переводит малый размер
выборки в честную поправку, а не прячет его.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Record:
    """Один ответ: уверенность проверяющей модели и верен ли он на самом деле."""

    confidence: float
    correct: bool


@dataclass
class Calibration:
    threshold: float
    alpha: float
    n_calibration: int
    # на калибровочной выборке
    coverage: float
    error_rate: float
    error_bound: float
    # на отложенной
    test_coverage: float | None = None
    test_error_rate: float | None = None
    test_n_delivered: int | None = None

    def report(self) -> str:
        lines = [
            f"порог отказа: {self.threshold:.2f}  (цель: не больше {self.alpha:.0%} ошибок)",
            f"калибровка на {self.n_calibration} примерах:",
            f"    выдаётся ответов: {self.coverage:.0%}",
            f"    ошибок среди выданных: {self.error_rate:.0%} "
            f"(верхняя граница {self.error_bound:.0%})",
        ]
        if self.test_coverage is not None:
            lines += [
                f"проверка на отложенной выборке ({self.test_n_delivered} выданных ответов):",
                f"    выдаётся ответов: {self.test_coverage:.0%}",
                f"    ошибок среди выданных: {self.test_error_rate:.0%}",
            ]
        return "\n".join(lines)


def clopper_pearson_upper(failures: int, n: int, confidence: float = 0.95) -> float:
    """Верхняя граница доли ошибок при n наблюдениях.

    Точная биномиальная граница через связь с бета-распределением. Без
    зависимостей: обратная функция беты найдена двоичным поиском по её
    регуляризованному неполному интегралу, вычисленному непрерывной дробью.
    """
    if n == 0:
        return 1.0
    if failures >= n:
        return 1.0

    # Граница p_u решает P(X <= failures | p_u) = 1 - confidence.
    # Через тождество P(X <= k | p) = 1 - I_p(k+1, n-k) это равносильно
    # I_p(k+1, n-k) = confidence. Ищем p двоичным поиском: I_p растёт по p.
    a, b = failures + 1, n - failures

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _betainc(a, b, mid) > confidence:
            hi = mid
        else:
            lo = mid
    return hi


def _betainc(a: float, b: float, x: float) -> float:
    """Регуляризованная неполная бета-функция I_x(a, b)."""
    import math

    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    log_beta = (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - log_beta) / a

    # Непрерывная дробь Лентца: сходится быстро при x < (a+1)/(a+b+2)
    if x > (a + 1) / (a + b + 2):
        return 1.0 - _betainc(b, a, 1 - x)

    tiny = 1e-30
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            numerator = 1.0
        elif i % 2 == 0:
            numerator = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            numerator = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))

        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        d = 1.0 / d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-12:
            break
    return front * (f - 1.0)


def calibrate(records: list[Record], *, alpha: float = 0.05,
              test: list[Record] | None = None,
              confidence: float = 0.95) -> Calibration:
    """Подбирает минимальный порог, при котором доля ошибок гарантированно ≤ alpha."""
    if not records:
        raise ValueError("нечего калибровать: пустой список")

    # Кандидаты — только наблюдавшиеся значения уверенности: между ними
    # решение не меняется, перебирать сетку бессмысленно.
    candidates = sorted({r.confidence for r in records} | {0.0, 1.0})

    best: Calibration | None = None
    for threshold in candidates:
        delivered = [r for r in records if r.confidence >= threshold]
        if not delivered:
            continue
        failures = sum(1 for r in delivered if not r.correct)
        bound = clopper_pearson_upper(failures, len(delivered), confidence)
        if bound <= alpha:
            best = Calibration(
                threshold=threshold,
                alpha=alpha,
                n_calibration=len(records),
                coverage=len(delivered) / len(records),
                error_rate=failures / len(delivered),
                error_bound=bound,
            )
            break  # кандидаты отсортированы, первый подошедший — минимальный

    if best is None:
        # Гарантия недостижима ни при каком пороге: данных слишком мало или
        # проверяющая модель не различает верные и неверные ответы.
        # Отказ должен называть, сколько не хватает: иначе он сообщает о
        # проблеме, но не о решении.
        need = next(
            (k for k in range(len(records), 2000)
             if clopper_pearson_upper(0, k, confidence) <= alpha),
            None,
        )
        hint = (f"При нуле ошибок обещание ≤{alpha:.0%} требует {need} выданных ответов "
                f"в калибровочной выборке, то есть примерно {need * 2} размеченных "
                f"примеров всего (половина уходит на проверку)."
                if need else "Такое обещание недостижимо на разумном числе примеров.")
        raise ValueError(
            f"ни один порог не даёт границу ошибки ≤ {alpha:.0%} при {len(records)} "
            f"примерах. {hint}"
        )

    if test:
        delivered = [r for r in test if r.confidence >= best.threshold]
        best.test_n_delivered = len(delivered)
        best.test_coverage = len(delivered) / len(test)
        best.test_error_rate = (
            sum(1 for r in delivered if not r.correct) / len(delivered) if delivered else 0.0
        )
    return best
