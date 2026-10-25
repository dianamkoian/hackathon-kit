"""Independent quality gates.

`check_quotes` is mechanical: a claimed quote either appears in the source or it
does not. No model, no cost, no judgment — it catches fabricated evidence
outright, which is the failure mode a verifier model is worst at spotting
because it is as willing to believe the quote as the model that invented it.

`check_signals` is mechanical in the other direction. Detectors find what
needs no judgment — a card number, a link, a request for an SMS code — and
the answer must account for every one the domain maps to an answer type.

`verify_with_model` is the judgment gate, and it runs on a *different* prompt
from the one that produced the answer. Self-verification with the same prompt
mostly reproduces the original error.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from .structured import ask_structured

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def resolve_path(data: Any, path: str) -> list[tuple[str, Any]]:
    """Resolve a dotted path, where `[]` means "every element".

    `items[].quote` over two items yields [("items[0].quote", ...), ("items[1].quote", ...)].
    """
    results: list[tuple[str, Any]] = [("$", data)]
    for part in path.split("."):
        is_array = part.endswith("[]")
        key = part[:-2] if is_array else part
        next_results: list[tuple[str, Any]] = []
        for prefix, node in results:
            if not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if is_array:
                if isinstance(value, list):
                    next_results += [(f"{prefix}.{key}[{i}]", v) for i, v in enumerate(value)]
            else:
                next_results.append((f"{prefix}.{key}", value))
        results = next_results
    return results


def check_quotes(data: Any, source_text: str, fields: tuple[str, ...]) -> list[str]:
    """Verify every claimed quote appears verbatim in the source.

    Whitespace and case are normalized; nothing else. Loosening this further
    (fuzzy match, token overlap) is how a grounding check quietly stops
    grounding anything.
    """
    if not fields:
        return []

    haystack = _normalize(source_text)
    problems: list[str] = []
    for field in fields:
        for location, value in resolve_path(data, field):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{location}: empty quote")
            elif _normalize(value) not in haystack:
                excerpt = value if len(value) <= 60 else value[:57] + "..."
                problems.append(f"{location}: quote not found in source — {excerpt!r}")
    return problems


# Порядок полей здесь — часть метода, а не оформление. Модель генерирует JSON
# слева направо, поэтому перечисление найденного идёт ДО вердикта: пока она не
# выписала, что видит в источнике, ей нечем заметить пропуск.
_VERIFIER_SCHEMA = {
    "type": "object",
    "required": ["found_in_source", "overlooked", "verdict", "confidence", "reason"],
    "properties": {
        "found_in_source": {"type": "array", "items": {"type": "string"}},
        # Пропуск обязан назвать свой тип: это единственное, чем код может
        # отличить настоящий пропуск от вежливого приветствия.
        "overlooked": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "quote"],
                "properties": {"type": {"type": "string"}, "quote": {"type": "string"}},
            },
        },
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
}


def _real_omissions(claimed, spec, data, source_text: str = "") -> list[str]:
    """Отсеивает мнимые пропуски тремя механическими проверками.

    Проверяющая модель охотно записывает в пропуски что угодно: вежливое
    приветствие, кусок уже найденной цитаты, собственную догадку. Если считать
    провалом любой непустой список, система отказывается отвечать почти на всё
    настоящее — проверено на живой модели.

    Поэтому решает код, а не модель. Пропуск засчитывается, только если:
      1. он назвал тип из словаря домена — а не «Добрый день!»;
      2. цитата дословно есть в исходном тексте — не выдумана;
      3. она не пересекается с цитатами, которые в ответе уже есть, — модели
         цитируют разными границами, и расхождение границ не пропуск.
    """
    allowed = {t.casefold() for t in getattr(spec, "omission_types", ())}
    present = [
        _normalize(value)
        for field in spec.quote_fields
        for _, value in resolve_path(data, field)
        if isinstance(value, str) and value.strip()
    ]
    haystack = _normalize(source_text)

    real: list[str] = []
    for item in claimed:
        if isinstance(item, dict):
            kind = str(item.get("type", "")).strip().casefold()
            quote = str(item.get("quote", "")).strip()
        else:
            kind, quote = "", str(item).strip()

        text = _normalize(quote)
        if not text:
            continue
        if allowed and kind not in allowed:
            continue  # не приём манипуляции, а просто фраза из сообщения
        if haystack and text not in haystack:
            continue  # цитаты нет в источнике — проверяющая её выдумала
        if any(text in q or q in text for q in present):
            continue  # то же самое другими границами цитаты
        if haystack and len(text) > 0.6 * len(haystack):
            # Приём — это фрагмент сообщения, а не всё сообщение. Когда модель
            # «цитирует» текст целиком, она не нашла приём, а пересказала вход.
            continue
        real.append(f"{kind or '?'}: {quote}")
    return real


def verify_with_model(provider, *, spec, fields: dict, data: Any, tier: str, trace):
    """Independent review of a candidate answer. Returns (passed, confidence, reason)."""
    import json

    user = (
        f"{spec.render_user(fields)}\n\n"
        f"A candidate answer was produced:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        "Review it in this order, and do not skip a step:\n"
        "1. `found_in_source`: quote EVERY statement in the input above that "
        "qualifies, working through the input from start to end. Do this before "
        "you look at the candidate answer again.\n"
        "2. `overlooked`: the entries from step 1 that are PRESENT in the input "
        "but ABSENT from the candidate answer. Each entry needs a `type` from "
        "this list — " + ", ".join(getattr(spec, "omission_types", ()) or ["—"])
        + " — AND a non-empty `quote` copied from the input.\n"
        "   This is NOT a checklist of types that do not occur. Do not list a "
        "type just because the input lacks it. If the candidate answer already "
        "covers everything you found, return an empty list. Greetings, "
        "signatures and neutral sentences are never entries here.\n"
        "3. `verdict`, `confidence` (0 to 1), `reason`.\n"
        "Be strict: plausible but unsupported is a fail."
    )
    verdict, _ = ask_structured(
        provider,
        system=spec.verifier_system,
        user=user,
        schema=_VERIFIER_SCHEMA,
        tier=tier,
        trace=trace,
        node="verify",
        attempts=2,
        max_tokens=1024,
    )
    confidence = float(verdict["confidence"])
    # A verifier that returns 1.0 on everything is a broken verifier; clamp only
    # the range, never the value, so miscalibration stays visible in the eval.
    confidence = min(max(confidence, 0.0), 1.0)

    # Булев вердикт модели НЕ право вето: он лишь снижает уверенность.
    # Замерено на Qwen 7B: модели, которой сказано «будь строгой», проще
    # ответить «не годится» почти на всё, и продукт замолкает. Решение принимает
    # откалиброванный порог по непрерывной уверенности — ради этого калибровка
    # и делалась. Булев ответ модели калиброванным не бывает.
    reason = verdict["reason"]
    if verdict["verdict"] != "pass":
        confidence *= 0.6
    passed = True

    # The consequence of an omission is enforced in code, not left to the model.
    # Measured: a verifier asked only for a verdict calls an answer "an exact
    # match" while a whole item is missing from it. Asking it to enumerate what
    # is absent is what makes the omission visible; refusing to pass a non-empty
    # list is what makes it matter.
    source_text = fields.get(spec.source_field, "") if spec.source_field else ""
    missing = _real_omissions(
        verdict.get("overlooked", []), spec, data, source_text
    )
    if missing:
        if getattr(spec, "omission_is_veto", False):
            passed = False
            confidence = min(confidence, 0.3)
        else:
            # Пропуск снижает уверенность, но не запрещает ответ: решает порог,
            # а он откалиброван под долю ошибок. Модель не должна иметь права
            # вето — она для этого недостаточно надёжна.
            confidence *= 0.75 ** len(missing)
        reason = f"пропущено: {'; '.join(str(m)[:60] for m in missing[:3])}"

    trace.add("recall", not missing,
              detail="ничего не пропущено" if not missing
              else f"пропусков: {len(missing)}"
                   + ("" if getattr(spec, "omission_is_veto", False)
                      else f", уверенность снижена до {confidence:.2f}"))
    return passed, confidence, reason


# ------------------------------------------------------------------ signals
#
# Observable surface features, not a fraud verdict. Every detector returns exact
# source slices with offsets, so the UI can highlight them; no model, no network.
# The domain owner decides how signals are used: `check_signals` fails only on
# the kinds the caller maps to an answer type, everything else stays a signal.
# Unsupported or obfuscated spellings may be missed; no match is not safety.

_SEP = "[  -]"
_MASK = "[*•xXхХ]"
_TRAILING = ".,!?;:)]}»"


def _signal(text: str, kind: str, start: int, end: int, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "quote": text[start:end], "start": start, "end": end, **extra}


def _overlaps(start: int, end: int, taken: list[dict[str, Any]]) -> bool:
    return any(s["start"] < end and start < s["end"] for s in taken)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        value = int(char)
        if i % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_CARD_GROUPED = re.compile(
    rf"(?<![\w*•])(?<!\d{_SEP})\d{{4}}(?:{_SEP}(?:\d{{4}}|{_MASK}{{4}})){{1,2}}"
    rf"{_SEP}\d{{4}}(?![\w*•])(?!{_SEP}\d)"
)
_CARD_SOLID = re.compile(rf"(?<![\w*•])\d{{4,6}}(?:\d{{6,8}}|{_MASK}{{4,8}})\d{{4}}(?![\w*•])")


def find_card_numbers(text: str) -> list[dict[str, Any]]:
    """Payment card numbers: 16 digits grouped by four or solid, or masked.

    `2202 2063 1234 5676`, `2202-2063-1234-5676`, `2202 **** 4411`,
    `220220******4411`. A solid run of 16 digits must also pass the Luhn
    checksum, otherwise every long order number would count. Masked numbers
    carry `masked: True`. A last-four mention like `*4411` is not matched:
    banks print those in ordinary notifications.
    """
    found: list[dict[str, Any]] = []
    for match in _CARD_GROUPED.finditer(text):
        masked = bool(re.search(_MASK, match.group()))
        if not masked and len(re.split(_SEP, match.group())) != 4:
            continue  # three groups of digits is not a card number
        found.append(_signal(text, "card_number", match.start(), match.end(), masked=masked))
    for match in _CARD_SOLID.finditer(text):
        quote = match.group()
        masked = not quote.isdigit()
        if len(quote) != 16 or (not masked and not _luhn_valid(quote)):
            continue
        found.append(_signal(text, "card_number", match.start(), match.end(), masked=masked))
    return found


_URL = re.compile(r"(?<![\w@])(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
# A bare domain needs a known TLD: without one, «т.е.» and «CDEK.Маркет» match.
_TLDS = ("ru|рф|su|com|net|org|info|biz|io|me|ly|cc|co|to|gl|ws|xyz|top|site|"
         "online|store|shop|app|link|click|pro|club|live|kz|by|ua|uz")
_BARE_DOMAIN = re.compile(
    rf"(?<![\w@./-])(?:[a-zа-яё0-9-]+\.)+(?:{_TLDS})(?![\w-])(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)


def _host(url: str) -> str:
    """Host part of a URL. Userinfo is dropped: in `bank.ru@evil.top` the host is evil.top."""
    start = url.find("://")
    start = 0 if start < 0 else start + 3
    end = len(url)
    for sep in "/?#":
        pos = url.find(sep, start)
        if pos != -1:
            end = min(end, pos)
    start = url.rfind("@", start, end) + 1 or start
    colon = url.find(":", start, end)
    return url[start:colon if colon != -1 else end]


def find_links(text: str) -> list[dict[str, Any]]:
    """Links with a scheme or `www.`, and bare domains with a known TLD.

    Each result also carries `domain`. Reputation, redirects and what the
    page shows are not checked: that needs the network.
    """
    found: list[dict[str, Any]] = []
    for pattern in (_URL, _BARE_DOMAIN):
        for match in pattern.finditer(text):
            quote = match.group().rstrip(_TRAILING)
            # A bare scheme is not a usable link.
            if re.fullmatch(r"(?:https?://|www\.)", quote, re.IGNORECASE):
                continue
            end = match.start() + len(quote)
            if _overlaps(match.start(), end, found):
                continue  # a domain inside a link already found
            found.append(_signal(text, "link", match.start(), end, domain=_host(quote)))
    return found


_ASK = (
    r"(?:(?:пришли|присылай|сообщи|сообщай|отправь|отправляй|назови|называй|продиктуй|"
    r"диктуй|перешли|пересылай|скинь|скажи|напиши|укажи|введи|вводи)(?:те)?|прислать|"
    r"сообщить|сообщать|отправить|назвать|называть|продиктовать|диктовать|переслать|"
    r"скинуть|сказать|написать|указать|ввести|вводить)"
)
_SECRET = (
    r"(?:(?:смс|sms)[- ]?код\w{0,2}"
    r"|код\w{0,2}\b[^.!?;\n]{0,40}?(?:\b(?:смс|sms|push|пуш)\b|\bсообщени\w*|\bподтвержд\w*"
    r"|\bприш[её]л\w*|\bприд[её]т|\bприходит|\bпоступ\w*)"
    r"(?:[^.!?;\n]{0,20}?\b(?:смс|sms)\b)?"  # stretch the quote to «в СМС» if it follows
    r"|парол\w*|cvv2?|cvc2?|пин[- ]?код\w{0,2}|pin"
    r"|код\w{0,2}\s+(?:с\s+)?(?:обратной|оборот)\w*"
    r"|(?:три|3)\s+цифры\s+(?:с\s+)?(?:обратной|оборот)\w*)"
)
_SECRET_REQUEST = re.compile(
    rf"\b(?P<ask>{_ASK})\b[^.!?;\n]{{0,60}}?\b{_SECRET}"
    rf"|\b{_SECRET}[^.!?;\n]{{0,40}}?\b(?P<ask_after>{_ASK})\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:не|нельзя|никогда|запрещено|запрещается)\b", re.IGNORECASE)


def find_secret_requests(text: str) -> list[dict[str, Any]]:
    """A request to tell, dictate, send or enter an SMS code, a password or CVV.

    Both orders: «продиктуйте код из СМС» and «код из СМС продиктуйте». A bare
    «код» counts only next to SMS/confirmation words, so «укажите код
    региона» does not. Negated advice («никому не сообщайте код», «банк
    никогда не просит назвать пароль») is skipped when the negation sits in
    the same clause before the verb. Quoted speech is not understood.
    """
    found: list[dict[str, Any]] = []
    for match in _SECRET_REQUEST.finditer(text):
        verb = "ask" if match.group("ask") is not None else "ask_after"
        clause = re.split(r"[.!?;,\n]", text[max(0, match.start(verb) - 30):match.start(verb)])[-1]
        if _NEGATION.search(clause):
            continue
        found.append(_signal(text, "secret_request", match.start(), match.end()))
    return found


_PHONE_RU = re.compile(
    r"(?<![\w+*])(?:\+7|8)[  -]?\(?\d{3}\)?[  -]?\d{3}[  -]?\d{2}"
    r"[  -]?\d{2}(?![\w*])"
)
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d[\d  ()-]{8,18}\d(?![\w*])")


def find_phone_numbers(text: str) -> list[dict[str, Any]]:
    """Russian numbers (+7 / 8 and ten digits, any usual grouping) and +country numbers.

    Masked numbers like `8 800 ***-**-**` are not matched: there is nothing
    to call. Digits that belong to a card number are not reported twice.
    """
    found: list[dict[str, Any]] = []
    taken = find_card_numbers(text)
    for pattern in (_PHONE_RU, _PHONE_INTL):
        for match in pattern.finditer(text):
            digits = re.sub(r"\D", "", match.group())
            if not 10 <= len(digits) <= 15 or _overlaps(match.start(), match.end(), taken + found):
                continue
            found.append(_signal(text, "phone_number", match.start(), match.end()))
    return found


# Latin letters that pass for Cyrillic ones, and the other way round.
_HOMOGLYPHS = {
    "LATIN": set("AaBCcEeHKkMOoPpTXxYy"),
    "CYRILLIC": set("АаВСсЕеНКкМОоРрТХхУу"),
}


def _script(char: str) -> str | None:
    if not char.isalpha():
        return None
    name = unicodedata.name(char, "")
    return next((s for s in ("CYRILLIC", "LATIN", "GREEK") if name.startswith(s)), None)


def _disguised(letters: list[tuple[str, str]], dominant: str | None) -> bool:
    """One switch of script inside a word: a disguise or an honest compound?

    «Bыплата», «БA3E»: the letters of one script are all look-alikes of the
    other — a disguise. «СберPay»: a lowercase word then a capitalised one —
    a brand compound. «ЮMoney»: «Ю» and «n» have no look-alikes — honest.
    """
    switch = next(i for i in range(1, len(letters)) if letters[i][1] != letters[i - 1][1])
    if any(ch.islower() for ch, _ in letters[:switch]) and letters[switch][0].isupper():
        return False
    by_script: dict[str, list[str]] = {}
    for char, script in letters:
        by_script.setdefault(script, []).append(char)
    for script, chars in by_script.items():
        minority = len(chars) * 2 <= len(letters)
        if ((script != dominant or minority)
                and all(ch in _HOMOGLYPHS.get(script, ()) for ch in chars)):
            return True
    return False


def find_mixed_script_words(text: str) -> list[dict[str, Any]]:
    """Words that mix Cyrillic, Latin and Greek letters, the way spam filters are dodged.

    Flagged: any Greek letter inside a mixed word («ΌБНΑРУЖЕΉ»), two or more
    switches of script («ПEҎЕХОДИTЕ»), or a single switch where one side is
    all look-alikes («БA3E»). Brand compounds like «СберPay» and «ЮMoney» are
    not flagged. A whole word in a foreign script («Ɓ») is not a mixed word.
    """
    counts = Counter(s for s in map(_script, text) if s)
    dominant = counts.most_common(1)[0][0] if counts else None
    found: list[dict[str, Any]] = []
    for match in re.finditer(r"\w+", text):
        letters = [(ch, s) for ch in match.group() if (s := _script(ch))]
        scripts = {s for _, s in letters}
        if len(scripts) < 2:
            continue
        switches = sum(a[1] != b[1] for a, b in zip(letters, letters[1:]))
        if "GREEK" in scripts or switches >= 2 or _disguised(letters, dominant):
            found.append(_signal(text, "mixed_script", match.start(), match.end()))
    return found


_INN = re.compile(r"\bИНН(?:/КПП)?[\s:№#]*(\d+)", re.IGNORECASE)
_INN_WEIGHTS = {
    10: ([2, 4, 10, 3, 5, 9, 4, 6, 8],),
    12: ([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]),
}


def _inn_valid(digits: str) -> bool:
    for weights in _INN_WEIGHTS[len(digits)]:
        control = sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10
        if control != int(digits[len(weights)]):
            return False
    return True


def find_invalid_inns(text: str) -> list[dict[str, Any]]:
    """An ИНН that cannot exist: wrong length or a failed control digit.

    A company has 10 digits, a sole trader or a person 12, and the last one
    or two digits are a checksum. «ИНН71384592» (8 digits) from a fake
    receipt is caught; a valid ИНН is not reported, because a valid number
    proves nothing about who sent the message.
    """
    found: list[dict[str, Any]] = []
    for match in _INN.finditer(text):
        digits = match.group(1)
        if len(digits) not in _INN_WEIGHTS:
            reason = f"{len(digits)} цифр, а у юрлица 10, у ИП и физлица 12"
        elif not _inn_valid(digits):
            reason = "не сходится контрольная сумма"
        else:
            continue
        found.append(_signal(text, "invalid_inn", match.start(), match.end(), reason=reason))
    return found


_DETECTORS = (find_card_numbers, find_links, find_secret_requests, find_phone_numbers,
              find_mixed_script_words, find_invalid_inns)


# Словесные признаки. Это по-прежнему наблюдение, а не догадка: фраза
# «переведи 18000» написана в тексте, её видно и можно процитировать по
# позиции. Детекторы выше ловят форму (карта, ссылка), эти — формулировку.
# Добавлены после замера: без них путь без модели считал чистым сообщение
# «мам, это я, новый номер, срочно переведи 18000».
_MONEY_PHRASE = re.compile(
    r"(?:перевед[иь]|скин[ьи]|отправ[ьи]|закин[ьи]|одолж[иь]|займ[иы])\w*"
    r"[^.!?\n]{0,40}?\d[\d\s]{2,}",
    re.IGNORECASE)
_URGENCY_PHRASE = re.compile(
    r"(?:срочно|прямо сейчас|немедленно|как можно быстрее|в течение часа|"
    r"иначе\s+(?:счёт|счет|аккаунт|карта|доступ)\w*\s+(?:будет\s+)?"
    r"(?:заблокир|закр|удал)\w*)",
    re.IGNORECASE)
# Завышенная оплата: слова про работу рядом с обещанием суммы. По отдельности
# ни то, ни другое не признак — вместе это самая частая схема в корпусе.
_TOO_GOOD_PHRASE = re.compile(
    r"(?:заработок|доход|оплата|зарплата|\bзп\b|прибыль|ставка|выплат\w*)"
    r"[^.!?\n]{0,30}?(?:от|до)\s*\d[\d\s]{2,}"
    r"|(?:от|до)\s*\d[\d\s]{2,}\s*(?:р|руб|₽|тыс|к|\$|бакс\w*)?\s*(?:в|за)\s*"
    r"(?:день|час|сутки|неделю|смену|месяц)",
    re.IGNORECASE)
_NEW_CONTACT_PHRASE = re.compile(
    r"(?:(?:у меня |это мой )?новый номер|пишу с (?:другого|чужого|нового) "
    r"(?:номера|телефона)|нов(?:ый|ая) (?:аккаунт|сим|симка)|"
    r"телефон (?:разбился|украли|потерял|слома)\w*|старый номер не работает)",
    re.IGNORECASE)


# Слухи: призыв разослать и «от вас скрывают». Сами по себе не обман, но
# новость с таким оформлением нельзя принимать без источника.
_SPREAD_CALL = re.compile(
    r"(?:перешлите|перешли|поделитесь|поделись|распространите|распространи|"
    r"отправьте|отправь|разошлите|расшарьте)[^.!?\n]{0,20}?"
    r"(?:всем|друз\w*|родн\w*|близк\w*|знаком\w*|по чатам|в чаты)",
    re.IGNORECASE)
_SUPPRESSION = re.compile(
    r"(?:власти|сми|телевизор\w*|правительство|чиновники)[^.!?\n]{0,25}?"
    r"(?:скрыва\w*|молч\w*|замалчива\w*)"
    r"|пока\s+(?:\w+\s+)?не\s+удалил\w*|скоро\s+удал\w*",
    re.IGNORECASE)


# Сообщение о событии: ссылка на источник, которого в тексте нет. Сигнал
# уровня doubt — факты мы не проверяем. Первое лицо («сообщаем») не считается:
# так пишут банки и сервисы в своих уведомлениях.
_NEWS_CLAIM = re.compile(
    r"\b(?:сообщает|сообщил[аи]?|по данным|по информации|стало известно|говорят|"
    r"пресс-служб\w*|официально|как сообщ(?!аем)\w*)\b",
    re.IGNORECASE)


def _phrase_signals(text: str) -> list[dict[str, Any]]:
    """Находит формулировки, которые видно глазом, и цитирует их по позиции."""
    found: list[dict[str, Any]] = []
    for kind, pattern in (("money_phrase", _MONEY_PHRASE),
                          ("urgency_phrase", _URGENCY_PHRASE),
                          ("new_contact_phrase", _NEW_CONTACT_PHRASE),
                          ("too_good_phrase", _TOO_GOOD_PHRASE),
                          ("spread_call", _SPREAD_CALL),
                          ("suppression", _SUPPRESSION),
                          ("news_claim", _NEWS_CLAIM)):
        for m in pattern.finditer(text):
            found.append({"kind": kind, "quote": m.group(0).strip(),
                          "start": m.start(), "end": m.end()})
    return found


def find_mechanical_signals(source_text: str) -> list[dict[str, Any]]:
    """All detectors at once, sorted by position.

    Every result has kind, quote, start and end, and quote == text[start:end].
    Kinds: card_number, link, secret_request, phone_number, mixed_script,
    invalid_inn. Extra keys: `masked` (card), `domain` (link), `reason` (ИНН).
    """
    signals = [signal for detect in _DETECTORS for signal in detect(source_text)]
    return _phrase_signals(source_text) + sorted(signals, key=lambda signal: (signal["start"], signal["kind"]))


def check_signals(
    data: Any, source_text: str, expected: dict[str, str | tuple[str, ...]], *,
    type_field: str, verdict_field: str | None = None, unsafe_verdicts: tuple[str, ...] = (),
) -> tuple[list[str], list[dict[str, Any]]]:
    """What the code sees in the source, the answer must name.

    The mirror of `check_quotes`: there the answer claims and the source
    confirms, here the source shows and the answer has to account for it.
    `expected` maps a signal kind to the answer type(s) that cover it, e.g.
    {"link": "external_link"}. It comes from the domain spec, as do
    `type_field` (dotted path to the types, e.g. "markers[].type") and
    `verdict_field`/`unsafe_verdicts` (e.g. "assessment", ("trust",)), so the
    core knows nothing about the theme. For every mapped kind found:
      - the answer must contain one of its types;
      - the verdict must not be one of `unsafe_verdicts`.
    Unmapped kinds never fail the gate. All signals come back for the trace
    and the UI.

    Returns (problems, signals); problems are strings, like `check_quotes`.
    """
    signals = find_mechanical_signals(source_text)
    present = {value for _, value in resolve_path(data, type_field) if isinstance(value, str)}
    verdicts = resolve_path(data, verdict_field) if verdict_field else []
    verdict = verdicts[0][1] if verdicts else None

    problems: list[str] = []
    for kind, wanted in expected.items():
        hits = [s for s in signals if s["kind"] == kind]
        if not hits:
            continue
        wanted = (wanted,) if isinstance(wanted, str) else tuple(wanted)
        quote = hits[0]["quote"]
        excerpt = quote if len(quote) <= 60 else quote[:57] + "..."
        more = f" (+{len(hits) - 1})" if len(hits) > 1 else ""
        if not present.intersection(wanted):
            problems.append(f"{type_field}: no {' or '.join(map(repr, wanted))} "
                            f"for {kind} {excerpt!r}{more}")
        if verdict in unsafe_verdicts:
            problems.append(f"{verdict_field}: {verdict!r} with {kind} {excerpt!r}{more}")
    return problems, signals


if __name__ == "__main__":
    import json
    from pathlib import Path

    def kinds(text: str) -> list[tuple[str, str]]:
        return [(s["kind"], s["quote"]) for s in find_mechanical_signals(text)]

    checked = 0

    def expect(actual: Any, wanted: Any) -> None:
        global checked
        assert actual == wanted, f"\n  got:    {actual!r}\n  wanted: {wanted!r}"
        checked += 1

    # Cards: grouped, masked, solid with Luhn; not three groups, not a last-four mention.
    # Словесный детектор видит и саму просьбу перевести, не только номер:
    # это ожидание обновлено после добавления _MONEY_PHRASE.
    expect(kinds("переведи на эту карту 2202 **** 4411"),
           [("money_phrase", "переведи на эту карту 2202"),
            ("card_number", "2202 **** 4411")])
    expect(kinds("карта 2202-2063-1234-5676."), [("card_number", "2202-2063-1234-5676")])
    expect(kinds("номер 4276380012345679 сбер"), [("card_number", "4276380012345679")])
    expect(kinds("заказ 4276380012345671"), [])  # 16 digits, Luhn fails: an order number
    expect(kinds("220220******4411"), [("card_number", "220220******4411")])
    expect(kinds("Покупка по карте *4411, 899 руб."), [])
    expect(kinds("код 2202 2063 4411"), [])

    # Links: scheme, www, bare domain with a known TLD; userinfo does not hide the host.
    expect(kinds("оплатите доставку по ссылке: prize-delivery-ru.top"),
           [("link", "prize-delivery-ru.top")])
    expect(kinds("Забрать промо http://примерссылки.ru."), [("link", "http://примерссылки.ru")])
    expect([s["domain"] for s in find_links("вход: https://sberbank.ru@evil.top/login?x=1")],
           ["evil.top"])
    expect(kinds("т.е. CDEK.Маркет, почта ivan@mail.ru"), [])

    # Requests for a secret, both orders; negated advice is not a request.
    expect(kinds("Для отмены продиктуйте код из СМС оператору."),
           [("secret_request", "продиктуйте код из СМС")])
    expect(kinds("Подтвердите владение: введите код, который придёт вам в СМС, на странице"),
           [("secret_request", "введите код, который придёт вам в СМС")])
    expect(kinds("Код из СМС назовите оператору"), [("secret_request", "Код из СМС назовите")])
    expect(kinds("Сообщите CVV с обратной стороны карты"), [("secret_request", "Сообщите CVV")])
    expect(kinds("Ваш код: 4821. Никому не сообщайте этот код, даже сотрудникам банка."), [])
    expect(kinds("Сотрудники банка никогда не просят сообщить код из СМС."), [])
    expect(kinds("Пароль от Wi-Fi в офисе поменяли. Укажите код региона."), [])

    # Phones, Russian and foreign; masked numbers are not callable.
    expect(kinds("звоните +7 (916) 123-45-67 или 8 800 555-35-35"),
           [("phone_number", "+7 (916) 123-45-67"), ("phone_number", "8 800 555-35-35")])
    expect(kinds("WhatsApp +375 29 123 45 67"), [("phone_number", "+375 29 123 45 67")])
    expect(kinds("позвоните по номеру 8 800 ***-**-** для отмены"), [])

    # Слухи: призыв разослать и «от вас скрывают».
    expect(kinds("Власти скрывают! Перешлите всем родным, пока пост не удалили"),
           [("spread_call", "Перешлите всем"), ("suppression", "Власти скрывают"),
            ("suppression", "пока пост не удалили")])
    expect(kinds("Поделитесь с друзьями. По телевизору молчат."),
           [("spread_call", "Поделитесь с друзьями"), ("suppression", "телевизору молчат")])
    expect(kinds("В Казани открыли станцию метро, сообщает пресс-служба мэрии."),
           [("news_claim", "сообщает"), ("news_claim", "пресс-служба")])
    expect(kinds("Сообщаем, что заказ доставлен."), [])

    # Mixed scripts: disguises yes, brand compounds no.
    expect(kinds("Bыплата одобрена"), [("mixed_script", "Bыплата")])
    expect(kinds("карта, ЮMoney и СберPay. Wi-Fi, iPhone 17"), [])

    # ИНН: wrong length, bad checksum; valid ones are silent.
    expect(kinds("ИНН 7707083893, ИНН/КПП 500100732259/770701001"), [])
    expect(kinds("ИНН: 7707083894"), [("invalid_inn", "ИНН: 7707083894")])

    # A real message: r-соня-01, a fake WildBerries receipt.
    real = Path(__file__).resolve().parent.parent / "evals" / "real" / "соня.jsonl"
    case = next(json.loads(line) for line in real.open(encoding="utf-8")
                if json.loads(line)["id"] == "r-соня-01")
    signals = find_mechanical_signals(case["text"])
    for s in signals:
        expect(case["text"][s["start"]:s["end"]], s["quote"])
    expect([s["quote"] for s in signals if s["kind"] != "mixed_script"],
           ["ИНН71384592", "http://примерссылки.ru"])
    expect([s["reason"] for s in signals if s["kind"] == "invalid_inn"],
           ["8 цифр, а у юрлица 10, у ИП и физлица 12"])
    expect([s["quote"] for s in signals if s["kind"] == "mixed_script"],
           ["ΌБНΑРУЖЕΉ", "БA3E", "ДΌТАЦИΌНHЫХ", "ПEҎЕХОДИTЕ", "ПРOВЕΡКЕ"])

    # The gate, with a mapping the way a domain spec would pass it.
    mapping = {"card_number": ("money_request", "code_request"),
               "link": "external_link", "secret_request": "code_request"}
    gate = dict(type_field="markers[].type", verdict_field="assessment",
                unsafe_verdicts=("trust",))
    naive = {"markers": [], "assessment": "trust"}
    problems, _ = check_signals(naive, case["text"], mapping, **gate)
    expect(problems, ["markers[].type: no 'external_link' for link 'http://примерссылки.ru'",
                      "assessment: 'trust' with link 'http://примерссылки.ru'"])
    good = {"markers": [{"type": "too_good"}, {"type": "external_link"}],
            "assessment": "distrust"}
    expect(check_signals(good, case["text"], mapping, **gate)[0], [])
    s02 = ("Служба безопасности Сбербанка. Для отмены продиктуйте код из СМС оператору.")
    expect(check_signals({"markers": [{"type": "authority"}], "assessment": "distrust"},
                         s02, mapping, **gate)[0],
           ["markers[].type: no 'code_request' for secret_request 'продиктуйте код из СМС'"])
    # Unmapped kinds are signals only: a bad ИНН alone never fails the gate.
    expect(check_signals({"markers": [], "assessment": "trust"}, "ИНН71384592",
                         mapping, **gate)[0], [])

    print(f"core.verify: {checked} checks passed\n")
    print(f"{case['id']}: {len(signals)} signals")
    for s in signals:
        extra = s.get("domain") or s.get("reason") or ""
        print(f"  {s['start']:>4}-{s['end']:<4} {s['kind']:<13} {s['quote']!r}"
              + (f"  ({extra})" if extra else ""))
    print(f"gate on a naive 'trust' answer: {problems}")
