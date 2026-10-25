# 007. Смоук перед пушем требует httpx, а в requirements.txt его нет

**От:** Соня
**Кому:** Диане (решение: `requirements.txt` — общий файл)
**Статус:** закрыт

## Что случилось

Pre-push хук запускает `scripts/smoke.sh`, а в нём
`from fastapi.testclient import TestClient`. `starlette.testclient` без пакета
`httpx` падает:

```
RuntimeError: The starlette.testclient module requires the httpx package to be installed.
```

`pip install -r requirements.txt` ставит fastapi и uvicorn, но не httpx. У
`anthropic~=1.0` в зависимостях `httpx2` — это другой пакет, `import httpx` он
не даёт. Значит, у любого, кто ставил окружение строго по `requirements.txt`,
хук падает на шаге «api imports and serves» ещё до своих изменений.

Отдельно: если `python3` в PATH — системный, а не из `.venv`, хук падает ещё
раньше, на `No module named 'fastapi'`. Хук берёт тот `python3`, который
первым стоит в PATH.

## Что сделала у себя

Поставила `httpx` только в свой локальный `.venv`
(`.venv/bin/python -m pip install "httpx~=0.28.1"`), `requirements.txt` не
трогала. Пушу с `.venv/bin` в начале PATH — хук проходит целиком, без
`SKIP_CHECKS`.

## Что нужно решить

Добавить ли в `requirements.txt` строку вроде
`httpx~=0.28.1  # только для smoke: fastapi.testclient`. Альтернатива —
написать в `RUNBOOK.md`, что для пуша нужен `pip install httpx` и активированный
`.venv`. Файлы общие, поэтому решение за тобой.

## Ответ
⟨заполняет Диана⟩


## Ответ Дианы

**Добавила `httpx~=0.28.1` в `requirements.txt`.** Ты права: у любого, кто ставил
окружение строго по файлу, хук падал ещё до своих изменений. Это моя недоделка —
я добавила смоук с `TestClient`, а зависимость не прописала.

Про `python3` из PATH: чинить в хуке не буду, иначе он начнёт угадывать чужое
окружение. Вместо этого `scripts/doctor.py` показывает, какой именно python
видит проект, — этого достаточно, чтобы заметить подмену.
