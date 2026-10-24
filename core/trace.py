"""Execution trace: what ran, what it cost, what it decided.

The trace is not debug output. Judges score orchestration they can see, so the
trace is a deliverable: it feeds the demo UI, the API response and the eval
report. Every node appends exactly one step.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    node: str
    ok: bool
    detail: str = ""
    model: str | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Trace:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    steps: list[Step] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def add(self, node: str, ok: bool, detail: str = "", **kw) -> Step:
        step = Step(node=node, ok=ok, detail=detail, **kw)
        self.steps.append(step)
        return step

    def add_completion(self, node: str, completion, ok: bool = True, detail: str = "") -> Step:
        """Record a model call, pulling cost and tokens off the Completion."""
        return self.add(
            node,
            ok,
            detail,
            model=completion.model,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
        )

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def model_latency_ms(self) -> float:
        """Time spent inside model calls — the part that scales with the cascade."""
        return sum(s.latency_ms for s in self.steps)

    @property
    def wall_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    @property
    def models_used(self) -> list[str]:
        seen: list[str] = []
        for s in self.steps:
            if s.model and s.model not in seen:
                seen.append(s.model)
        return seen

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "cost_usd": round(self.cost_usd, 6),
            "model_latency_ms": round(self.model_latency_ms, 1),
            "wall_ms": round(self.wall_ms, 1),
            "models_used": self.models_used,
            "steps": [asdict(s) for s in self.steps],
        }

    def render(self) -> str:
        """Plain-text trace for the terminal and for pasting into the README."""
        lines = [f"trace {self.task_id}  ${self.cost_usd:.5f}  {self.model_latency_ms:.0f}ms"]
        for s in self.steps:
            mark = "ok  " if s.ok else "FAIL"
            model = f" [{s.model}]" if s.model else ""
            cost = f" ${s.cost_usd:.5f}" if s.cost_usd else ""
            lines.append(f"  {mark} {s.node}{model}{cost}  {s.detail}")
        return "\n".join(lines)
