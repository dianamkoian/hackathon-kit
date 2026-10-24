"""Распознавание речи: звук -> текст.

Нужно для любой темы, где на входе созвон, голосовое сообщение или видео.
Дальше текст идёт в тот же каскад, что и всё остальное.

Файл читается стандартными средствами Python, если это обычная запись
16 кГц моно. Это не придирка: иначе для чтения звука понадобилась бы
отдельная программа (ffmpeg), а на хакатоне лишняя установка — это лишний
способ всё сломать за час до сдачи.
"""
from __future__ import annotations

import os
import time
import wave
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = os.getenv("ASR_MODEL", "mlx-community/whisper-small-mlx")


@dataclass
class Transcript:
    text: str
    seconds: float       # длительность записи
    latency_ms: float    # сколько заняло распознавание
    model: str

    @property
    def speed(self) -> float:
        """Во сколько раз быстрее реального времени. Больше 1 — хорошо."""
        return self.seconds / (self.latency_ms / 1000) if self.latency_ms else 0.0


def _read_simple_wav(path: Path):
    """Читает запись 16 кГц моно. Возвращает (звук, длительность) или None."""
    import numpy as np

    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
                return None
            if handle.getframerate() != 16000:
                return None
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError):
        return None

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, frames / 16000


def transcribe(path: str | Path, *, model: str | None = None,
               language: str | None = "ru") -> Transcript:
    """Превращает звуковой файл в текст."""
    import mlx_whisper

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"нет файла: {path}")

    model = model or DEFAULT_MODEL
    simple = _read_simple_wav(path)
    # Если формат не такой — отдаём путь, тогда звук читает сам mlx_whisper,
    # но ему для этого нужен ffmpeg.
    source, seconds = simple if simple else (str(path), 0.0)

    started = time.monotonic()
    result = mlx_whisper.transcribe(
        source, path_or_hf_repo=model, language=language, verbose=False
    )
    latency_ms = (time.monotonic() - started) * 1000

    return Transcript(
        text=result["text"].strip(),
        seconds=seconds or sum(s["end"] - s["start"] for s in result.get("segments", [])),
        latency_ms=latency_ms,
        model=model,
    )
