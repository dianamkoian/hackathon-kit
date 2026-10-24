"""Example domain — replace this file on the day, keep everything else.

The task: pull commitments (who / what / when) out of a short transcript, each
carrying a verbatim quote as evidence. It is a placeholder, but not an arbitrary
one — it exercises every part of the kit (schema, mechanical grounding gate,
model verifier, routing, abstention), so swapping in the real theme is a matter
of rewriting `SPEC`, the grader and the fixtures.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.pipeline import TaskSpec
from core.structured import extract_json

CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "cases.jsonl"

SCHEMA = {
    "type": "object",
    "required": ["commitments"],
    "properties": {
        "commitments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["who", "what", "when", "quote"],
                "properties": {
                    "who": {"type": "string"},
                    "what": {"type": "string"},
                    "when": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        }
    },
}

SYSTEM = (
    "You extract commitments from meeting transcripts.\n"
    "A commitment is a speaker stating they will personally do something. "
    "A hedged offer ('I could look at it'), an explicit refusal ('I can't promise "
    "that'), a conditional offer ('I can if nobody else does') and a commitment "
    "that is later retracted are NOT commitments.\n"
    "Every commitment must carry `quote`: the speaker's own words, copied "
    "verbatim from the transcript. Never paraphrase a quote. "
    "Use \"unspecified\" for `when` when no time is given."
)

VERIFIER_SYSTEM = (
    "You review commitment extractions for correctness. You did not produce the "
    "answer and you have no stake in it. Check for invented commitments, hedged "
    "or retracted statements counted as commitments, missed real commitments, "
    "and quotes that do not match the transcript."
)

USER_TEMPLATE = "Transcript:\n---\n{text}\n---\n\nExtract the commitments."

# Phrases that mark the transcripts models actually get wrong: hedges,
# retractions, conditionals. One cheap string scan decides the tier, so the
# easy mass never touches the expensive model.
_HARD_MARKERS = (
    "actually", "not before", "can't promise", "cannot promise",
    "i think", "if nobody", "soon as", "i'd like",
)


def route(fields: dict) -> list[str]:
    text = fields.get("text", "").casefold()
    if any(marker in text for marker in _HARD_MARKERS):
        return ["strong"]
    return ["cheap", "strong"]


_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS.sub(" ", str(value)).strip().casefold()


def _key(answer: Any) -> list[tuple[str, str, str]]:
    if not isinstance(answer, dict):
        return []
    return sorted(
        (_norm(c.get("who", "")), _norm(c.get("what", "")), _norm(c.get("when", "")))
        for c in answer.get("commitments", [])
        if isinstance(c, dict)
    )


def grade(expected: Any, produced: Any) -> bool:
    """Exact set match on (who, what, when); quote wording is not graded.

    Honest only because the mock returns the fixtures' own phrasing. A real
    model paraphrases `what`, so on the day either loosen this to the fields
    that must match exactly (who, when) or add an LLM judge — and say in the
    README which one the numbers came from.
    """
    if produced is None:
        return False
    return _key(expected) == _key(produced)


SPEC = TaskSpec(
    name="commitments",
    system=SYSTEM,
    user_template=USER_TEMPLATE,
    schema=SCHEMA,
    verifier_system=VERIFIER_SYSTEM,
    quote_fields=("commitments[].quote",),
    source_field="text",
    router=route,
    grade=grade,
)


def load_cases(path: Path | str = CASES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ mock backend

def _degrade(expected: dict) -> dict:
    """A plausible wrong answer: right shape, wrong content.

    On an empty expectation it invents a commitment, because "finds something
    that is not there" is exactly how models fail on a conditional offer.
    """
    items = [dict(c) for c in expected.get("commitments", [])]
    if not items:
        return {
            "commitments": [
                {
                    "who": "Marina",
                    "what": "take the compliance checklist",
                    "when": "unspecified",
                    "quote": "I can if nobody else does.",
                }
            ]
        }
    items[0]["who"] = "the team"
    return {"commitments": items[:-1] or items[:1]}


def _fabricate(expected: dict) -> dict:
    """Right answer, invented evidence — what the mechanical gate exists to catch."""
    items = [dict(c) for c in expected.get("commitments", [])]
    if items:
        items[0]["quote"] = "We will handle it by the end of the week."
    return {"commitments": items}


def make_mock_handler(cases: list[dict]):
    """Deterministic offline backend scripted by each case's `mock` field.

    `mock_verifier` is keyed by verifier-call number ("1" is the first review of
    that case), not by tier — so a fixture can model a false alarm that the
    escalation then recovers from, which is what verifier noise actually looks
    like. The counters make the handler stateful within a process: one cascade
    run per case, which is what the eval does.
    """
    by_text = {case["text"]: case for case in cases}
    verifier_calls: dict[str, int] = {}

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
                "wrong": _degrade(case["expected"]),
                "fabricate": _fabricate(case["expected"]),
            }[mode]
            return json.dumps(answer, ensure_ascii=False)

        marker = "A candidate answer was produced:"
        candidate = extract_json(user[user.index(marker) + len(marker):])
        call_number = verifier_calls[case["id"]] = verifier_calls.get(case["id"], 0) + 1
        override = case.get("mock_verifier", {}).get(str(call_number))
        if override == "pass":
            return json.dumps({"verdict": "pass", "confidence": 0.72,
                               "reason": "reads as supported by the transcript"})
        if override == "fail":
            return json.dumps({"verdict": "fail", "confidence": 0.41,
                               "reason": "cannot confirm the deadline from the transcript"})
        if grade(case["expected"], candidate):
            return json.dumps({"verdict": "pass", "confidence": 0.91,
                               "reason": "every commitment is stated and quoted"})
        return json.dumps({"verdict": "fail", "confidence": 0.28,
                           "reason": "a listed commitment is not supported by the transcript"})

    return handler
