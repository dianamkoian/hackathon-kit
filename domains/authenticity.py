"""Тема 2 «Интернет больше не доказательство»: «Второе мнение».

Человек вставляет подозрительное сообщение. Система не угадывает «фейк / не
фейк», а отвечает тремя вещами:

  assessment — trust | doubt | distrust
  markers    — признаки манипуляции, КАЖДЫЙ с дословной цитатой из сообщения
  check_id   — одна самая дешёвая проверка из каталога (domains/checks.json),
               которая снимает сомнение быстрее всего

Как это ложится на барьеры ядра:
  - схема с ретраем       — ответ всегда в одном формате для фронта и API;
  - механический гейт     — маркер без дословной цитаты отклоняется: модель не
                            может «почувствовать» мошенничество, она обязана
                            показать, где оно в тексте;
  - верификатор           — отдельный промпт перечисляет ВСЕ маркеры в тексте,
                            пропущенный маркер = провал (код в core/verify.py);
  - отказ ниже порога     — честное «не уверены», и тогда проверка из каталога
                            и есть ответ продукта.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.pipeline import TaskSpec
from core.structured import extract_json

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE.parent / "evals" / "cases_authenticity.jsonl"
CHECKS_PATH = HERE / "checks.json"

CHECKS: list[dict] = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
CHECKS_BY_ID = {c["id"]: c for c in CHECKS}

MARKER_TYPES = [
    "money_request",   # просьба перевести / оплатить / одолжить
    "urgency",         # срочно, прямо сейчас, иначе заблокируют
    "new_contact",     # новый номер, чужой телефон, сменил аккаунт
    "authority",       # банк, СБ, полиция, ЦБ, госорган, начальник
    "secrecy",         # никому не говорите, тайна следствия
    "code_request",    # код из СМС, пароль, данные карты
    "external_link",   # ссылка на оплату / вход / получение
    "prepayment",      # заплатите, чтобы получить выигрыш, работу, выплату
    "too_good",        # приз, компенсация, доходность, лёгкий заработок
    "unverifiable_proof",  # скриншот, фото, голосовое как «доказательство»
    "remote_access",   # установить приложение удалённого доступа
]

# Ключ ответа НЕ называется "verdict": по этому ключу mock-бэкенд отличает
# схему верификатора от схемы ответа.
SCHEMA = {
    "type": "object",
    "required": ["markers", "assessment", "check_id", "explanation"],
    "properties": {
        "markers": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "quote"],
                "properties": {
                    "type": {"type": "string", "enum": MARKER_TYPES},
                    "quote": {"type": "string"},
                },
            },
        },
        "assessment": {"type": "string", "enum": ["trust", "doubt", "distrust"]},
        "check_id": {"type": "string", "enum": [c["id"] for c in CHECKS]},
        "explanation": {"type": "string"},
    },
}


def _catalog() -> str:
    return "\n".join(
        f"- {c['id']}: когда — {c['doubt']}; действие — {c['action']}"
        for c in CHECKS
    )


SYSTEM = (
    "Ты — «второе мнение» для человека, который получил подозрительное сообщение. "
    "Ты не детектор фейков и не угадываешь. Твоя задача — найти в тексте признаки "
    "манипуляции и выбрать ОДНУ самую дешёвую проверку, которая снимет сомнение.\n\n"
    "Порядок работы (поля JSON заполняй в этом же порядке):\n"
    "1. `markers`: пройди сообщение от начала до конца и выпиши каждый признак "
    "манипуляции. У каждого — `type` из списка и `quote`: точная цитата из "
    "сообщения, символ в символ. Не пересказывай. Нет цитаты — нет маркера.\n"
    "2. `assessment`:\n"
    "   - trust — маркеров нет, сообщение не просит ничего рискованного;\n"
    "   - distrust — узнаваемая схема: просьба кода, предоплата за выигрыш, "
    "«служба безопасности», срочный перевод от «близкого» с нового номера и т.п.;\n"
    "   - doubt — есть маркеры, но сообщение может быть и настоящим. Это нормальный "
    "ответ, а не провал: по тексту нельзя отличить, и человеку нужна проверка.\n"
    "3. `check_id`: одна проверка из каталога — самая быстрая из тех, что реально "
    "снимают сомнение для этого сообщения. Для trust — C00.\n"
    "4. `explanation`: одна-две фразы на русском, простым языком, без запугивания.\n\n"
    f"Типы маркеров: {', '.join(MARKER_TYPES)}.\n\n"
    f"Каталог проверок:\n{_catalog()}"
)

MARKER_TYPES = (
    "money_request", "urgency", "new_contact", "authority", "secrecy",
    "code_request", "external_link", "prepayment", "too_good",
    "unverifiable_proof", "remote_access", "obfuscation",
)

VERIFIER_SYSTEM = (
    "Ты проверяешь чужой разбор подозрительного сообщения. Ты его не писал и не "
    "заинтересован в нём. «Подходящее утверждение» здесь — любой фрагмент "
    "сообщения, который является признаком манипуляции: просьба денег или кода, "
    "срочность, новый контакт, ссылка, ссылка на авторитет, требование секретности, "
    "предоплата, слишком хорошее предложение, непроверяемое «доказательство». "
    "Выписывай их дословными цитатами.\n"
    "Провал, если: пропущен признак, который меняет оценку; trust поставлен при "
    "наличии просьбы денег, кода или ссылки; distrust поставлен обычному бытовому "
    "сообщению; проверка не снимает именно это сомнение (например, обратный поиск "
    "картинки для звонка от «банка»)."
)

USER_TEMPLATE = "Сообщение:\n---\n{text}\n---\n\nРазбери его."

# Сообщения, где дешёвая модель ошибается чаще всего: подмена контакта,
# «доказательства» в виде скриншотов и голосовых, смена реквизитов. Одна
# строковая проверка — и такие сразу уходят на сильный тир.
_HARD_MARKERS = (
    "новый номер", "новом номере", "сменил", "сменила", "номера друга",
    "с чужого", "голосов", "расшифровк", "скрин", "фото", "реквизит",
)


def route(fields: dict) -> list[str]:
    text = fields.get("text", "").casefold()
    if any(marker in text for marker in _HARD_MARKERS):
        return ["strong"]
    return ["cheap", "strong"]


def grade(expected: Any, produced: Any) -> bool:
    """Верно = совпали оценка и выбранная проверка.

    Набор маркеров не грейдится: разные модели режут цитаты по-разному. Полноту
    маркеров проверяет верификатор, а дословность — механический гейт.
    """
    if not isinstance(produced, dict) or not isinstance(expected, dict):
        return False
    return (produced.get("assessment") == expected.get("assessment")
            and produced.get("check_id") == expected.get("check_id"))


def check_card(check_id: str | None) -> dict | None:
    """Карточка проверки для фронта: действие, время, почему работает."""
    return CHECKS_BY_ID.get(check_id or "")


# Отказ каскада для пользователя — это не «ошибка», а оценка doubt: система не
# смогла обосновать вывод, значит человеку нужна проверка. Универсальная — пауза.
ABSTAIN_CHECK_ID = "C12"


def present(data: Any) -> dict:
    """То, что показывает продукт: оценка + одна карточка проверки."""
    if not isinstance(data, dict):
        return {"assessment": "doubt", "check": check_card(ABSTAIN_CHECK_ID),
                "abstained": True}
    # «Признаков не найдено» — не то же самое, что «сомневаюсь». Если валить их
    # в одно состояние, продукт выглядит так, будто подозревает даже «привет»,
    # и человек перестаёт верить предупреждениям вообще. Но и «всё чисто»
    # сказать нельзя: отсутствие улик не доказательство невиновности.
    no_markers = not data.get("markers")
    state = "clean" if no_markers else data.get("assessment", "doubt")
    return {"assessment": data.get("assessment"), "state": state,
            "check": check_card(data.get("check_id")), "abstained": False}


SPEC = TaskSpec(
    name="authenticity",
    system=SYSTEM,
    user_template=USER_TEMPLATE,
    schema=SCHEMA,
    verifier_system=VERIFIER_SYSTEM,
    quote_fields=("markers[].quote",),
    # Пропуском считается только приём из этого списка. Без него проверяющая
    # модель записывает в пропуски «Добрый день!» и продукт молчит.
    omission_types=MARKER_TYPES,
    # Что код видит в тексте — ответ обязан назвать. Телефоны, смешанные
    # алфавиты и неверный ИНН намеренно НЕ включены: это сигналы для
    # трассировки и подсветки, а не повод забраковать ответ.
    signal_markers={
        "card_number": ("money_request", "code_request"),
        "link": "external_link",
        "secret_request": "code_request",
    },
    signal_type_field="markers[].type",
    signal_verdict_field="assessment",
    signal_unsafe_verdicts=("trust",),
    source_field="text",
    router=route,
    grade=grade,
)


def load_cases(path: Path | str = CASES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ mock backend

_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS.sub(" ", str(value)).strip().casefold()


def _degrade(expected: dict, text: str) -> dict:
    """Правдоподобная ошибка той формы, какая бывает у моделей.

    Подозрительное -> «всё нормально» (пропуск маркеров), обычное -> паника.
    """
    if expected["assessment"] == "trust":
        return {
            "markers": [{"type": "urgency", "quote": text[:20]}],
            "assessment": "distrust",
            "check_id": "C12",
            "explanation": "Похоже на давление срочностью.",
        }
    return {
        "markers": [],
        "assessment": "trust",
        "check_id": "C00",
        "explanation": "Обычное сообщение.",
    }


def _fabricate(expected: dict) -> dict:
    """Верный вывод, выдуманное доказательство — ровно то, что ловит гейт цитат."""
    answer = json.loads(json.dumps(expected))
    answer["markers"] = list(answer["markers"]) + [
        {"type": "urgency", "quote": "ответьте в течение 15 минут"}
    ]
    return answer


def make_mock_handler(cases: list[dict]):
    """Детерминированный офлайн-бэкенд, управляемый полем `mock` каждого кейса."""
    by_text = {case["text"]: case for case in cases}

    def handler(system: str, user: str, model: str, schema: dict | None) -> str:
        tier = "cheap" if model.endswith("cheap") else "strong"
        case = next((c for text, c in by_text.items() if text in user), None)
        if case is None:
            raise RuntimeError("mock backend: no fixture matches this prompt")

        is_verifier = bool(schema and "verdict" in schema.get("properties", {}))
        if not is_verifier:
            mode = case["mock"][tier]
            answer = {
                "correct": case["expected"],
                "wrong": _degrade(case["expected"], case["text"]),
                "fabricate": _fabricate(case["expected"]),
            }[mode]
            return json.dumps(answer, ensure_ascii=False)

        marker = "A candidate answer was produced:"
        candidate = extract_json(user[user.index(marker) + len(marker):]) or {}
        expected_quotes = [m["quote"] for m in case["expected"]["markers"]]
        answer_quotes = {_norm(m.get("quote", "")) for m in candidate.get("markers", [])
                         if isinstance(m, dict)}
        missing = [q for q in expected_quotes if _norm(q) not in answer_quotes]
        base = {"found_in_source": expected_quotes, "overlooked": missing}
        if grade(case["expected"], candidate):
            return json.dumps({**base, "verdict": "pass", "confidence": 0.88,
                               "reason": "оценка и проверка опираются на найденные маркеры"},
                              ensure_ascii=False)
        return json.dumps({**base, "verdict": "fail", "confidence": 0.3,
                           "reason": "оценка не соответствует маркерам в сообщении"},
                          ensure_ascii=False)

    return handler


# --------------------------------------------------------- ответ без модели

# Что видит код, то он и называет. Соответствие сигнала детектора маркеру
# ответа: это не догадка модели, а наблюдаемый факт в тексте.
_SIGNAL_TO_MARKER = {
    "card_number": "money_request",
    "secret_request": "code_request",
    "link": "external_link",
    "mixed_script": "obfuscation",
    "money_phrase": "money_request",
    "urgency_phrase": "urgency",
    "new_contact_phrase": "new_contact",
    "too_good_phrase": "too_good",
}

# Сигналы, каждый из которых сам по себе — основание не доверять сообщению.
# Просьба кода и номер карты в чужом сообщении не имеют безобидного прочтения.
_DECISIVE = ("secret_request", "card_number")


def mechanical_answer(text: str) -> dict | None:
    """Разбор сообщения без единого вызова модели.

    Зачем это нулевой уровень каскада, а не запасной план. Часть приёмов
    наблюдаема, а не угадываема: ссылка в тексте либо есть, либо нет; просьба
    продиктовать код либо есть, либо нет. На такое не нужна модель — нужен
    детектор. Такой ответ бесплатен, мгновенен и не галлюцинирует по
    построению: каждая цитата взята из текста по позиции, а не написана.

    Возвращает None, если наблюдаемых признаков нет: тогда «безопасно» решать
    нельзя, и дальше идёт модель. Отсутствие улик не доказательство
    невиновности.
    """
    from core.verify import find_mechanical_signals

    signals = find_mechanical_signals(text)
    markers, kinds = [], set()
    for s in signals:
        marker_type = _SIGNAL_TO_MARKER.get(s["kind"])
        if not marker_type:
            continue  # телефон, смешанный алфавит, ИНН — для подсветки, не для вердикта
        kinds.add(s["kind"])
        markers.append({"type": marker_type, "quote": s["quote"]})

    if not markers:
        return None

    decisive = kinds & set(_DECISIVE)
    # Сочетание «просьба денег + новый контакт» решающее, хотя каждый
    # признак по отдельности встречается и в честных сообщениях.
    combo = {"money_phrase", "new_contact_phrase"} <= kinds
    assessment = "distrust" if (decisive or combo) else "doubt"

    if "secret_request" in kinds:
        check_id = "C11"           # просят код из СМС, пароль, данные карты
    elif "card_number" in kinds:
        check_id = "C01"           # просьба о деньгах — перезвонить на старый номер
    elif {"money_phrase", "new_contact_phrase"} & kinds:
        check_id = "C01"           # просьба денег с нового контакта
    elif "link" in kinds:
        check_id = "C04"           # ссылка — открыть сервис вручную
    else:
        check_id = "C18"           # подмена букв — текст маскируется от фильтров

    names = ", ".join(sorted(kinds))
    return {
        "markers": markers,
        "assessment": assessment,
        "check_id": check_id,
        "explanation": f"разобрано без модели по наблюдаемым признакам: {names}",
    }


def decisive_answer(text: str) -> dict | None:
    """Ответ, для которого модель не нужна вовсе.

    Отдаётся только когда найден признак без безобидного прочтения: просьба
    продиктовать код или чужой номер карты в присланном сообщении. Ссылка сама
    по себе сюда не попадает — ссылки бывают в настоящих письмах, и решать по
    ней одной нельзя.

    Смысл в том, что такие вещи не угадывают, а видят. Поэтому ответ готов за
    миллисекунды, а не за десятки секунд, и в нём нечего галлюцинировать.
    """
    from core.verify import find_mechanical_signals

    signals = find_mechanical_signals(text)
    kinds = {s["kind"] for s in signals}
    if not kinds & set(_DECISIVE):
        return None

    answer = mechanical_answer(text)
    if answer is None:
        return None
    answer["explanation"] = (
        "ответ дан без вызова модели: найден признак, у которого нет "
        f"безобидного прочтения — {', '.join(sorted(kinds & set(_DECISIVE)))}"
    )
    return answer
