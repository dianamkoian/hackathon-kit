"""Run the eval set and print the table the defence needs.

Two arms on the same cases, the same prompt and the same strong model:

  baseline — one call, no gates. What a team without orchestration ships.
  cascade  — route, validate, ground, verify, escalate, abstain.

The headline is not accuracy. It is **wrong answers delivered**: a system that
declines 1 case in 10 and is right on the rest is worth more than one that
always answers and is quietly wrong twice as often. Report both, plus cost and
latency, and say which number you are actually winning on.

    python3 -m evals.run                  # mock backend, no key needed
    LLM_BACKEND=anthropic python3 -m evals.run
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.pipeline import run, run_baseline
from core.providers import get_provider
from domains.commitments import SPEC, load_cases, make_mock_handler

REPORT_DIR = Path(__file__).resolve().parent


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, written out so the definition is not in doubt."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil(q * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


@dataclass
class ArmStats:
    name: str
    n: int = 0
    correct: int = 0
    answered: int = 0
    wrong_delivered: int = 0
    cost: float = 0.0
    latencies: list[float] = field(default_factory=list)

    def record(self, *, is_correct: bool, answered: bool, cost: float, latency: float) -> None:
        self.n += 1
        self.cost += cost
        self.latencies.append(latency)
        if answered:
            self.answered += 1
            if is_correct:
                self.correct += 1
            else:
                self.wrong_delivered += 1

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def accuracy_answered(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    @property
    def coverage(self) -> float:
        return self.answered / self.n if self.n else 0.0

    def to_dict(self) -> dict:
        return {
            "arm": self.name,
            "cases": self.n,
            "accuracy_overall": round(self.accuracy, 4),
            "accuracy_on_answered": round(self.accuracy_answered, 4),
            "coverage": round(self.coverage, 4),
            "wrong_delivered": self.wrong_delivered,
            "abstained": self.n - self.answered,
            "cost_total_usd": round(self.cost, 6),
            "cost_per_case_usd": round(self.cost / self.n, 6) if self.n else 0.0,
            "p50_model_latency_ms": round(percentile(self.latencies, 0.50), 1),
            "p95_model_latency_ms": round(percentile(self.latencies, 0.95), 1),
        }


def _table(arms: list[ArmStats]) -> str:
    head = (
        "| arm | accuracy (all) | accuracy (answered) | coverage | "
        "wrong delivered | $/case | p95 latency |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {a.name} | {a.accuracy:.1%} | {a.accuracy_answered:.1%} | {a.coverage:.1%} | "
        f"{a.wrong_delivered} | ${a.cost / a.n if a.n else 0:.5f} | "
        f"{percentile(a.latencies, 0.95):.0f} ms |"
        for a in arms
    ]
    return "\n".join([head, *rows])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=None, help="path to a .jsonl case file")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    parser.add_argument("--arm", choices=["both", "cascade", "baseline"], default="both")
    parser.add_argument("--trace", action="store_true", help="print the trace of every case")
    args = parser.parse_args()

    cases = load_cases(args.cases) if args.cases else load_cases()
    if args.limit:
        cases = cases[: args.limit]

    provider = get_provider(mock_handler=make_mock_handler(cases))
    arms: dict[str, ArmStats] = {}
    if args.arm in ("baseline", "both"):
        arms["baseline"] = ArmStats("baseline")
    if args.arm in ("cascade", "both"):
        arms["cascade"] = ArmStats("cascade")

    per_case: list[dict] = []
    started = time.monotonic()

    for case in cases:
        fields = {"text": case["text"]}
        row = {"id": case["id"], "difficulty": case["difficulty"]}

        for name, stats in arms.items():
            runner = run_baseline if name == "baseline" else run
            try:
                result = runner(SPEC, fields, provider)
            except Exception as exc:  # noqa: BLE001 - a dead case must not kill the run
                # The library is allowed to raise; the harness is not allowed to
                # stop. Count it as an unanswered case and keep going, or one
                # bad transcript costs you the whole table at hour seven.
                print(f"  ! {case['id']}/{name}: {type(exc).__name__}: {exc}")
                stats.record(is_correct=False, answered=False, cost=0.0, latency=0.0)
                row[name] = {"status": "error", "correct": False, "cost_usd": 0.0,
                             "model_latency_ms": 0.0, "tier_used": None,
                             "reason": f"{type(exc).__name__}: {exc}"}
                continue
            is_correct = bool(SPEC.grade(case["expected"], result.data))
            stats.record(
                is_correct=is_correct,
                answered=result.answered,
                cost=result.trace.cost_usd,
                latency=result.trace.model_latency_ms,
            )
            row[name] = {
                "status": result.status,
                "correct": is_correct,
                "cost_usd": round(result.trace.cost_usd, 6),
                "model_latency_ms": round(result.trace.model_latency_ms, 1),
                "tier_used": result.tier_used,
                "reason": result.reason,
            }
            if args.trace:
                print(f"\n=== {case['id']} / {name}")
                print(result.trace.render())

        per_case.append(row)

    ordered = list(arms.values())
    table = _table(ordered)
    wall = time.monotonic() - started

    print()
    print(table)
    print()
    if len(cases) < 20:
        print(f"note: {len(cases)} cases — p95 is effectively the slowest case here. "
              "Scale to 20-30 before quoting it at judges.")

    if "cascade" in arms and "baseline" in arms:
        b, c = arms["baseline"], arms["cascade"]
        cost_change = (c.cost / b.cost - 1) if b.cost else 0.0
        latency_change = (
            percentile(c.latencies, 0.95) / percentile(b.latencies, 0.95) - 1
            if percentile(b.latencies, 0.95) else 0.0
        )
        print(f"cascade vs baseline: wrong answers {b.wrong_delivered} -> {c.wrong_delivered}, "
              f"cost {cost_change:+.0%}, p95 latency {latency_change:+.0%}, "
              f"abstentions {c.n - c.answered}/{c.n}")

    print("\nper case:")
    for row in per_case:
        bits = [f"{row['id']} ({row['difficulty']:<9})"]
        for name in arms:
            cell = row[name]
            mark = "+" if cell["correct"] else ("." if cell["status"] == "abstained" else "x")
            bits.append(f"{name}:{mark}{cell['status']:<10}")
        print("  " + "  ".join(bits))
    print("\n  + correct   x wrong answer delivered   . abstained")

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": provider.name,
        "spec": SPEC.name,
        "cases": len(cases),
        "wall_seconds": round(wall, 2),
        "arms": [a.to_dict() for a in ordered],
        "per_case": per_case,
    }
    (REPORT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "report.md").write_text(
        f"# Eval report\n\n"
        f"- backend: `{provider.name}`  \n- spec: `{SPEC.name}`  \n"
        f"- cases: {len(cases)}  \n- generated: {report['generated_at']}\n\n"
        f"{table}\n\n"
        f"Headline: wrong answers delivered went from "
        f"{arms['baseline'].wrong_delivered if 'baseline' in arms else '-'} to "
        f"{arms['cascade'].wrong_delivered if 'cascade' in arms else '-'}.\n",
        encoding="utf-8",
    )
    print(f"\nwrote {REPORT_DIR / 'report.md'} and report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
