"""Two independent quality gates.

`check_quotes` is mechanical: a claimed quote either appears in the source or it
does not. No model, no cost, no judgment — it catches fabricated evidence
outright, which is the failure mode a verifier model is worst at spotting
because it is as willing to believe the quote as the model that invented it.

`verify_with_model` is the judgment gate, and it runs on a *different* prompt
from the one that produced the answer. Self-verification with the same prompt
mostly reproduces the original error.
"""
from __future__ import annotations

import re
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


# Observable surface features, not a fraud verdict. The domain owner decides
# how these are used; their presence must not automatically block a message.
_URL = re.compile(r"(?<!\w)(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_SMS_REQUEST = re.compile(
    r"\b(?:пришли(?:те)?|сообщи(?:те)?|отправь(?:те)?|назови(?:те)?|"
    r"продиктуй(?:те)?|перешли(?:те)?|скинь(?:те)?)\b"
    r"[^.!?;\n]{0,60}?\bкод(?:а|ы)?\b[^.!?;\n]{0,30}?\b(?:смс|sms)\b",
    re.IGNORECASE,
)
_NUMBER_RUN = re.compile(r"(?<!\w)[0-9](?:[0-9 \u00a0-]*[0-9])?(?!\w)")


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


def find_mechanical_signals(source_text: str) -> list[dict[str, Any]]:
    """Find URL, explicit Russian SMS-code request and card-number candidates.

    Every result contains kind, quote, start and end (Python string offsets).
    Quotes are exact source slices; there are no network or model calls.
    Card candidates require 13-19 ASCII digits, a Luhn checksum and a nearby
    Russian card mention; this does NOT establish that a card exists.
    URL reputation, quoted speech, intent and authenticity are not determined.
    Unsupported/obfuscated spellings may be missed; no matches is not safety.
    """
    signals: list[dict[str, Any]] = []

    def add(kind: str, start: int, end: int) -> None:
        signals.append({"kind": kind, "quote": source_text[start:end],
                        "start": start, "end": end})

    for match in _URL.finditer(source_text):
        quote = match.group().rstrip(".,!?;:)]}»")
        # A bare scheme is not a usable URL signal.
        if re.fullmatch(r"(?:https?://|www\.)", quote, re.IGNORECASE):
            continue
        add("url", match.start(), match.start() + len(quote))

    for match in _SMS_REQUEST.finditer(source_text):
        prefix = source_text[max(0, match.start() - 40):match.start()]
        if re.search(r"\b(?:не|нельзя|запрещено)\s*$", prefix, re.IGNORECASE):
            continue
        add("sms_code_request", match.start(), match.end())

    for match in _NUMBER_RUN.finditer(source_text):
        digits = re.sub(r"[ \u00a0-]", "", match.group())
        context = source_text[max(0, match.start() - 40):match.start()]
        if (13 <= len(digits) <= 19 and len(set(digits)) > 1
                and re.search(r"\bкарт(?:а|ы|е|у|ой|ою)\b", context, re.IGNORECASE)
                and _luhn_valid(digits)):
            add("card_number_candidate", match.start(), match.end())

    return sorted(signals, key=lambda signal: (signal["start"], signal["kind"]))
