"""Чтение текста со скриншота.

Самый частый способ переслать подозрительное сообщение — скриншот. Человек не
копирует текст, он делает снимок экрана и отправляет его близкому. Значит
продукт обязан принимать картинку, иначе он не работает там, где нужен.

Дальше всё то же самое: распознанный текст идёт тем же путём, что набранный
руками. Никакой отдельной логики для картинок нет, и это сознательно —
меньше кода, меньше мест, где расходится поведение.

Модель локальная: скриншот переписки может содержать что угодно, и отправлять
его наружу незачем.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

MODEL = os.getenv("VISION_MODEL", "Qwen/Qwen2-VL-2B-Instruct")
PROMPT = ("Выпиши дословно весь текст с этого изображения. "
          "Только текст, без пояснений и без описания картинки.")

_cached: tuple | None = None


@dataclass
class Seen:
    text: str
    latency_ms: float
    model: str


def _load():
    """Модель грузится один раз на процесс: загрузка дороже самого чтения."""
    global _cached
    if _cached is None:
        import torch
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        processor = AutoProcessor.from_pretrained(MODEL)
        model = Qwen2VLForConditionalGeneration.from_pretrained(
            MODEL, dtype=torch.float16 if device == "mps" else torch.float32,
            device_map=device)
        _cached = (processor, model, device)
    return _cached


def read_text(path: str | Path, *, max_tokens: int = 320) -> Seen:
    """Снимает текст с изображения."""
    import torch
    from PIL import Image

    processor, model, device = _load()
    image = Image.open(path).convert("RGB")
    messages = [{"role": "user", "content": [{"type": "image"},
                                             {"type": "text", "text": PROMPT}]}]
    prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(text=[prompt], images=[image], return_tensors="pt").to(device)

    started = time.monotonic()
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    text = processor.batch_decode(out[:, inputs.input_ids.shape[1]:],
                                  skip_special_tokens=True)[0]
    return Seen(text=text.strip(), latency_ms=(time.monotonic() - started) * 1000,
                model=MODEL)
