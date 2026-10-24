"""Structured output: extract, validate, retry with the error in the prompt.

Written out rather than pulled from `jsonschema` on purpose — it is ~80 lines,
removes a dependency from the critical path, and the validation messages are
what gets fed back to the model, so their wording matters.
"""
from __future__ import annotations

from typing import Any

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


class StructuredError(RuntimeError):
    """Model never produced output matching the schema."""


def extract_json(text: str) -> Any:
    """Pull the first complete JSON value out of a model response.

    Models wrap JSON in prose or code fences. Brace counting beats a regex here
    because it respects strings and escapes, so a `}` inside a quoted value does
    not end the object early.
    """
    import json

    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        raise StructuredError("no JSON object or array in response")

    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    # Balanced braces but invalid JSON — a trailing comma, a
                    # single quote, an unescaped newline. Small models do this
                    # constantly. It is a model failure, so it must raise the
                    # retryable error, not escape as a JSONDecodeError and kill
                    # the run.
                    raise StructuredError(
                        f"malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
                    ) from exc

    raise StructuredError("JSON value is not closed")


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """Check a JSON Schema subset: type, required, properties, items, enum, minItems.

    Returns human-readable problems — they go straight back to the model.
    """
    problems: list[str] = []

    expected = schema.get("type")
    if expected:
        py_type = _TYPES[expected]
        # bool is a subclass of int in Python; a boolean is not a number here.
        if expected in ("number", "integer") and isinstance(instance, bool):
            problems.append(f"{path}: expected {expected}, got boolean")
            return problems
        if not isinstance(instance, py_type):
            got = type(instance).__name__
            problems.append(f"{path}: expected {expected}, got {got}")
            return problems

    if "enum" in schema and instance not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        problems.append(f"{path}: {instance!r} is not one of [{allowed}]")

    if expected == "object":
        for key in schema.get("required", []):
            if key not in instance:
                problems.append(f"{path}: missing required key {key!r}")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                problems += validate(instance[key], subschema, f"{path}.{key}")

    if expected == "array":
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < min_items:
            problems.append(f"{path}: needs at least {min_items} item(s), got {len(instance)}")
        if "items" in schema:
            for i, item in enumerate(instance):
                problems += validate(item, schema["items"], f"{path}[{i}]")

    return problems


_JSON_INSTRUCTION = (
    "Reply with a single JSON value and nothing else — no prose, no code fence.\n"
    "It must satisfy this JSON Schema:\n{schema}"
)


def ask_structured(
    provider,
    *,
    system: str,
    user: str,
    schema: dict,
    tier: str,
    trace,
    node: str,
    attempts: int = 2,
    max_tokens: int = 4096,
):
    """Ask until the answer parses and validates, feeding each failure back.

    The retry carries the specific validation error rather than a generic "try
    again" — that is the difference between a model fixing the field it got
    wrong and it rerolling the whole answer.
    """
    import json

    prompt = f"{user}\n\n" + _JSON_INSTRUCTION.format(schema=json.dumps(schema, ensure_ascii=False))
    last_problems: list[str] = []

    for attempt in range(1, attempts + 1):
        completion = provider.complete(
            system=system, user=prompt, tier=tier, schema=schema, max_tokens=max_tokens
        )
        try:
            data = extract_json(completion.text)
        except StructuredError as exc:
            last_problems = [str(exc)]
        else:
            last_problems = validate(data, schema)
            if not last_problems:
                detail = "valid" if attempt == 1 else f"valid after {attempt} attempts"
                trace.add_completion(node, completion, detail=detail)
                return data, completion

        trace.add_completion(
            node, completion, ok=False,
            detail=f"attempt {attempt} invalid: {'; '.join(last_problems[:3])}",
        )
        prompt = (
            f"{prompt}\n\nYour previous answer was rejected:\n"
            + "\n".join(f"- {p}" for p in last_problems)
            + "\nReturn corrected JSON."
        )

    raise StructuredError(f"schema never satisfied in {attempts} attempts: {last_problems}")
