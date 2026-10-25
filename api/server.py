"""HTTP surface. Two reasons it exists, both worth points.

1. "Is the system integrable?" is a criterion. A curl one-liner in the README
   answers it in a way a UI never does.
2. The demo page is a client of the same endpoint judges can call themselves,
   so nothing in the demo is special-cased.

    uvicorn api.server:app --reload --port 8000
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.pipeline import confidence_floor, run, run_baseline
from core.deadline import DeadlineRunner
from core.profile import Profile, clarifying_question, missing_context
from core.safeword import SafeWord, prompt_for
from core.store import Store
from core.providers import get_provider
import api.ollama  # noqa: F401 — registers LLM_BACKEND=ollama (local models on Windows)
from domains.authenticity import (
    SPEC, decisive_answer, load_cases, make_mock_handler, mechanical_answer,
    present,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
ROOT_TRAINING = Path(__file__).resolve().parent.parent / "evals" / "training.jsonl"

app = FastAPI(title="hackathon-kit", version="0.1.0")

# One provider for the process. The mock handler needs the fixtures, so the
# offline backend only answers prompts built from a known case — by design:
# a demo that silently invents answers for unseen input is worse than one that
# says it cannot.
# Хранилище проверок. Две продуктовые задачи: «вы не первый, кому это
# прислали» и растущий корпус настоящих сообщений — размеченных примеров
# нам не хватает, а этот источник пополняется сам.
_store = Store()
_cases = load_cases()
_training = [json.loads(l) for l in (ROOT_TRAINING.read_text(encoding='utf-8').splitlines())
             if l.strip()] if ROOT_TRAINING.exists() else []
# Контрольный вопрос — одна семья на демо. Аккаунтов нет и не нужно.
_safeword: SafeWord | None = None
# Личный контекст: только то, что человек ввёл сам, и только для подсказок.
_profile = Profile()
# Модели даётся срок и один поток: она считает по очереди, и очередь
# из пяти запросов не ускоряет ни один, зато растит ожидание.
_deadline = DeadlineRunner()
# Сервис обязан подняться, даже если модель настроена неверно. На площадке
# не будет времени чинить окружение: пусть работают детекторы и честный отказ,
# а не «сервер не стартовал». Деградация, а не падение.
try:
    _provider = get_provider(mock_handler=make_mock_handler(_cases))
    _provider_error: str | None = None
except Exception as exc:  # noqa: BLE001 — любая беда с моделью не должна ронять сервис
    _provider = None
    _provider_error = f"{type(exc).__name__}: {exc}"
    print(f"[server] модель недоступна ({_provider_error}); "
          f"работаем только на детекторах")


class ProfileRequest(BaseModel):
    people: list[dict] = []
    employer: str = ""
    bank: str = ""
    services: list[str] = []
    expecting: list[str] = []


class SafeWordRequest(BaseModel):
    person: str
    question: str
    answer: str


class RunRequest(BaseModel):
    text: str
    arm: str = "cascade"  # "cascade" | "baseline"


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "backend": _provider.name if _provider else "mechanical-only",
        "model_error": _provider_error,
        "spec": SPEC.name,
        "confidence_floor": confidence_floor(),
    }


@app.get("/samples")
def samples() -> list[dict]:
    """Fixture transcripts, so the demo page has something to click."""
    return [
        {"id": c["id"], "difficulty": c["difficulty"], "text": c["text"]}
        for c in _cases
    ]


@app.post("/run")
def run_endpoint(req: RunRequest) -> dict:
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text is empty")
    if req.arm not in ("cascade", "baseline"):
        raise HTTPException(status_code=422, detail="arm must be cascade or baseline")
    runner = run if req.arm == "cascade" else run_baseline
    seen_before = _store.seen_count(req.text)

    # Нулевой уровень идёт ПЕРВЫМ, а не в запасе. Когда детекторы нашли
    # признак, у которого нет безобидного прочтения — просьбу кода или чужой
    # номер карты, — ответ готов за миллисекунды и модель не нужна вовсе.
    # Это и скорость, и принцип: такие вещи не угадывают, их видят.
    if req.arm == "cascade":
        quick = decisive_answer(req.text)
        if quick is not None:
            _store.save(
                text=req.text, status="mechanical",
                assessment=quick.get("assessment"), markers=quick.get("markers"),
                confidence=None, backend="mechanical", cost_usd=0.0, latency_ms=0.0,
            )
            return {
                "arm": req.arm, "seen_before": seen_before, "status": "mechanical",
                "answered": True, "data": quick, "confidence": None,
                "reason": quick["explanation"], "tier_used": "mechanical",
                "product": present(quick),
                "safeword": prompt_for(_safeword, quick.get("markers")),
                "question": (lambda q: q.to_dict() if q else None)(
                    clarifying_question(quick.get("markers"), req.text, _profile)),
                "trace": {"task_id": "mechanical", "cost_usd": 0.0,
                          "model_latency_ms": 0.0, "wall_ms": 0.0, "models_used": [],
                          "steps": [{"node": "signals", "ok": True,
                                     "detail": quick["explanation"], "model": None,
                                     "cost_usd": 0.0, "latency_ms": 0.0,
                                     "input_tokens": 0, "output_tokens": 0}]},
            }

    try:
        if _provider is None:
            # Модели нет вовсе — ниже отработает механический разбор.
            raise RuntimeError(f"модель недоступна: {_provider_error}")
        attempt = _deadline.run(lambda: runner(SPEC, {"text": req.text}, _provider))
        if not attempt.ok:
            # Занята, не уложилась или упала — для человека это одно и то же:
            # ответ нужен сейчас. Отдаём то, что видят детекторы.
            raise RuntimeError(attempt.reason)
        result = attempt.value
    except RuntimeError as exc:
        # Модели нет или она не знает этот текст. Часть приёмов наблюдаема
        # детекторами, без модели — отвечаем тем, что код реально видит, и
        # честно помечаем ответ как механический.
        fallback = mechanical_answer(req.text)
        if fallback is None:
            # Наблюдаемых признаков нет. Это не «безопасно»: отсутствие улик
            # не доказательство невиновности, и говорить надо именно так.
            fallback = {
                "markers": [], "assessment": "doubt", "check_id": "C00",
                "explanation": "приёмов манипуляции в тексте не найдено. "
                               "Это не гарантия: мы проверяем приёмы, а не факты",
            }
        _store.save(
            text=req.text, status="mechanical",
            assessment=fallback.get("assessment"), markers=fallback.get("markers"),
            confidence=None, backend="mechanical", cost_usd=0.0, latency_ms=0.0,
        )
        return {
            "arm": req.arm, "seen_before": seen_before, "status": "mechanical",
            "answered": True, "data": fallback, "confidence": None,
            "reason": f"{fallback['explanation']} ({exc})",
            "tier_used": "mechanical", "product": present(fallback),
            "safeword": prompt_for(_safeword, fallback.get("markers")),
            "question": (lambda q: q.to_dict() if q else None)(
                clarifying_question(fallback.get("markers"), req.text, _profile)),
            "trace": {"task_id": "mechanical", "cost_usd": 0.0,
                      "model_latency_ms": 0.0, "wall_ms": 0.0, "models_used": [],
                      "steps": [{"node": "signals", "ok": True,
                                 "detail": fallback["explanation"], "model": None,
                                 "cost_usd": 0.0, "latency_ms": 0.0,
                                 "input_tokens": 0, "output_tokens": 0}]},
        }

    data = result.data if isinstance(result.data, dict) else {}
    _store.save(
        text=req.text, status=result.status, assessment=data.get("assessment"),
        markers=data.get("markers"), confidence=result.confidence,
        backend=_provider.name, cost_usd=result.trace.cost_usd,
        latency_ms=result.trace.model_latency_ms,
    )
    data_markers = (result.data or {}).get("markers") if isinstance(result.data, dict) else None
    return {"arm": req.arm, "seen_before": seen_before,
            **result.to_dict(), "product": present(result.data),
            "safeword": prompt_for(_safeword, data_markers),
            "question": (lambda q: q.to_dict() if q else None)(
                clarifying_question(data_markers, req.text, _profile))}


@app.post("/run-audio")
async def run_audio(file: UploadFile = File(...)) -> dict:
    """Голосовое сообщение: распознаём речь и дальше тот же путь, что у текста.

    Зачем это именно здесь, а не отдельным продуктом: голосом обманывают чаще,
    чем текстом, и как раз в голосовых «мама, это я» звучит убедительнее, чем
    читается. Распознавание локальное — голос никуда не уходит с машины.
    """
    import tempfile

    from core.asr import transcribe

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="пустой файл")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="файл больше 25 МБ")

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        heard = transcribe(tmp_path)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="распознавание речи недоступно: pip install mlx-whisper (только Apple Silicon)",
        ) from None
    except Exception as exc:  # noqa: BLE001 — показать причину, а не 500
        raise HTTPException(status_code=422, detail=f"не удалось распознать: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not heard.text.strip():
        raise HTTPException(status_code=422, detail="в записи не распознана речь")

    body = run_endpoint(RunRequest(text=heard.text))
    body["heard"] = {
        "text": heard.text,
        "seconds": round(heard.seconds, 1),
        "latency_ms": round(heard.latency_ms),
        "model": heard.model,
    }
    return body


@app.post("/profile")
def set_profile(req: ProfileRequest) -> dict:
    """Что человек рассказал о себе: близкие, работа, банк, чего он ждёт.

    Нужно для двух вещей: не задавать вопрос, ответ на который уже известен,
    и делать подсказки конкретными. Никуда не уходит.
    """
    global _profile
    _profile = Profile.from_dict(req.model_dump())
    return {"ok": True, "profile": _profile.to_dict(),
            "still_missing": missing_context(_profile)}


@app.get("/profile")
def get_profile() -> dict:
    return {"profile": _profile.to_dict(), "still_missing": missing_context(_profile)}


@app.post("/safeword")
def set_safeword(req: SafeWordRequest) -> dict:
    """Контрольный вопрос для близкого человека.

    Ответ не хранится — только его отпечаток: это семейная тайна, и знать её
    серверу незачем.
    """
    global _safeword
    try:
        _safeword = SafeWord.create(req.person, req.question, req.answer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **_safeword.to_dict()}


@app.get("/safeword")
def get_safeword() -> dict:
    return _safeword.to_dict() if _safeword else {"person": None, "question": None}


@app.get("/training")
def training(n: int = 10) -> list[dict]:
    """Настоящие сообщения для тренировки, без подсказки о правильном ответе."""
    import random

    if not _training:
        raise HTTPException(status_code=503,
                            detail="набор для тренировки не собран: "
                                   "python3 scripts/build_training_set.py")
    picked = random.sample(_training, min(n, len(_training)))
    return [{"id": r["id"], "text": r["text"]} for r in picked]


@app.post("/training/check")
def training_check(req: dict) -> dict:
    """Сверяет догадку человека с правильным ответом и показывает приёмы."""
    row = next((r for r in _training if r["id"] == req.get("id")), None)
    if row is None:
        raise HTTPException(status_code=404, detail="такого примера нет")
    guess = str(req.get("guess", "")).strip().casefold()
    return {
        "id": row["id"], "truth": row["truth"],
        "correct": guess == row["truth"].casefold(),
        "markers": row.get("markers", []),
        "text": row["text"],
    }


@app.get("/stats")
def stats() -> dict:
    """Сколько проверок прошло через систему и чем они кончались."""
    return {**_store.stats().to_dict(), "model_calls": _deadline.stats}


@app.get("/slides")
def slides() -> FileResponse:
    """Колода для защиты по той же ссылке, что и демо."""
    return FileResponse(WEB_DIR / "slides.html")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
