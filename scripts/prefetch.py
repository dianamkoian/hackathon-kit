#!/usr/bin/env python3
"""Pre-download and verify local models before the event.

The point is not convenience. Venue wi-fi on the morning of a hackathon is
shared by everyone in the room, and a 5 GB pull at 09:00 does not finish. Models
you did not fetch the night before are models you do not have.

    python3 scripts/prefetch.py                 # what is cached, what is missing
    python3 scripts/prefetch.py --download llm asr
    python3 scripts/prefetch.py --verify        # load each one and actually run it

Downloading is never implicit: nothing leaves the network without --download.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"


@dataclass
class Entry:
    group: str
    repo: str
    gb: float  # approximate on-disk size; check before trusting a disk budget
    why: str
    kind: str  # mlx-llm | mlx-asr | embed | plain


REGISTRY: list[Entry] = [
    # Cheap tier of the cascade. 1.5B is weak at schema-following; 7-8B at 4-bit
    # is the smallest size that holds structured output reliably, and still fits
    # alongside a browser and an IDE in 24 GB of unified memory.
    Entry("llm", "mlx-community/Qwen2.5-1.5B-Instruct-4bit", 0.9,
          "fastest local tier; fine for routing and classification", "mlx-llm"),
    Entry("llm", "mlx-community/Qwen3-4B-4bit", 2.3,
          "good balance when 7B is too slow for a live demo", "mlx-llm"),
    Entry("llm", "mlx-community/Qwen2.5-7B-Instruct-4bit", 4.3,
          "smallest local model that follows a JSON schema dependably", "mlx-llm"),
    Entry("llm", "mlx-community/Qwen3-8B-4bit", 4.7,
          "stronger local extractor; slower per token", "mlx-llm"),

    # Any theme with meetings, calls, voice notes or video starts here.
    Entry("asr", "mlx-community/whisper-small-mlx", 0.5,
          "fast transcription; good enough for a demo", "mlx-asr"),
    Entry("asr", "mlx-community/whisper-large-v3-turbo", 1.6,
          "accurate transcription, still real-time on M-series", "mlx-asr"),

    # Semantic search, dedup, clustering, routing by similarity, RAG.
    Entry("embed", "intfloat/multilingual-e5-base", 1.1,
          "multilingual embeddings; the workhorse of any retrieval feature", "embed"),
    Entry("embed", "BAAI/bge-m3", 2.3,
          "stronger multilingual embeddings, long inputs", "embed"),
    Entry("embed", "BAAI/bge-reranker-v2-m3", 2.3,
          "reranker — the cheapest large win in RAG quality", "plain"),
]


def cache_dir_for(repo: str) -> Path:
    return HF_CACHE / ("models--" + repo.replace("/", "--"))


def is_cached(repo: str) -> bool:
    directory = cache_dir_for(repo)
    return directory.is_dir() and any(directory.rglob("*.safetensors")) or (
        directory.is_dir() and any(directory.rglob("*.npz"))
    )


def du_gb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / 1024**3


def cmd_status(entries: list[Entry]) -> int:
    print(f"{'':2} {'group':6} {'size':>6}  {'repo':52} why")
    missing_gb = 0.0
    for e in entries:
        cached = is_cached(e.repo)
        if not cached:
            missing_gb += e.gb
        mark = "OK" if cached else "--"
        size = f"{du_gb(cache_dir_for(e.repo)):.1f}G" if cached else f"~{e.gb:.1f}G"
        print(f"{mark:2} {e.group:6} {size:>6}  {e.repo:52} {e.why}")
    print(f"\nmissing: ~{missing_gb:.1f} GB. Download the night before, not at the venue.")
    return 0


def cmd_download(entries: list[Entry]) -> int:
    from huggingface_hub import snapshot_download

    for e in entries:
        if is_cached(e.repo):
            print(f"[cached] {e.repo}")
            continue
        print(f"[fetch ] {e.repo} (~{e.gb:.1f} GB)")
        started = time.monotonic()
        snapshot_download(e.repo)
        print(f"[done  ] {e.repo} in {time.monotonic() - started:.0f}s")
    return 0


def _verify_mlx_llm(repo: str) -> str:
    from mlx_lm import generate, load

    model, tokenizer = load(repo)
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "Reply with the single word: ready"}],
        add_generation_prompt=True, tokenize=False,
    )
    started = time.monotonic()
    out = generate(model, tokenizer, prompt=prompt, max_tokens=8, verbose=False)
    return f"{time.monotonic() - started:.1f}s -> {out.strip()[:40]!r}"


def _verify_embed(repo: str) -> str:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(repo)
    started = time.monotonic()
    vectors = model.encode(["hello", "привет"])
    return f"{time.monotonic() - started:.1f}s -> shape {vectors.shape}"


def _verify_mlx_asr(repo: str) -> str:
    import mlx_whisper  # noqa: F401  (import is the check; no audio fixture here)

    return "package present, weights cached (no audio fixture to decode)"


VERIFIERS = {"mlx-llm": _verify_mlx_llm, "embed": _verify_embed, "mlx-asr": _verify_mlx_asr}

PIP_HINT = {
    "mlx-llm": "pip install mlx-lm",
    "mlx-asr": "pip install mlx-whisper",
    "embed": "pip install sentence-transformers",
}


def cmd_verify(entries: list[Entry]) -> int:
    failures = 0
    for e in entries:
        if not is_cached(e.repo):
            print(f"SKIP  {e.repo}: not downloaded")
            continue
        verifier = VERIFIERS.get(e.kind)
        if verifier is None:
            print(f"OK    {e.repo}: weights present (no runtime check for this kind)")
            continue
        try:
            print(f"OK    {e.repo}: {verifier(e.repo)}")
        except ImportError:
            print(f"SKIP  {e.repo}: {PIP_HINT.get(e.kind, 'missing package')}")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"FAIL  {e.repo}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"\n{failures} model(s) failed to run. Fix this now, not on the day.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", nargs="*", metavar="GROUP",
                        help="actually fetch: llm, asr, embed (no value = all)")
    parser.add_argument("--verify", action="store_true",
                        help="load every cached model and run it once")
    parser.add_argument("--group", nargs="*", default=None, metavar="GROUP")
    args = parser.parse_args()

    groups = args.download if args.download else args.group
    entries = [e for e in REGISTRY if not groups or e.group in groups]
    if not entries:
        print(f"no entries for {groups}; groups are: llm, asr, embed")
        return 2

    if args.download is not None:
        return cmd_download(entries)
    if args.verify:
        return cmd_verify(entries)
    return cmd_status(entries)


if __name__ == "__main__":
    raise SystemExit(main())
