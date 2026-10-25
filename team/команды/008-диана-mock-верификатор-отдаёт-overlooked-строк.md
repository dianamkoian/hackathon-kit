# 008. mock-верификатор отдаёт overlooked строками, схема ждёт объекты

**От:** Alexander Rosushchan
**Кому:** диана (зона: core/pipeline.py, core/providers.py, domains/<тема>.py, пороги)
**Статус:** открыта

## Что сделать
mock-верификатор отдаёт overlooked строками, схема ждёт объекты

## Зачем
после 73effac _VERIFIER_SCHEMA (core/verify.py:87) требует overlooked: [{type, quote}], а make_mock_handler (domains/authenticity.py:268) кладёт туда строки missing. Воспроизведение: uvicorn, POST /run с примером «Голосовое от брата (расшифровка)…» — в trace два verify 'invalid: $.overlooked[0]: expected object, got str', затем 'verifier unreadable' и abstain. Метрики eval не меняются (там и так отказ), но барьер полноты пропал из трассировки: вместо 'пропущено: 1' жюри видит сбой верификатора. Это видно на экране в ленте барьеров. Вероятная правка — overlooked = [{'type': m['type'], 'quote': m['quote']} for m in expected markers if missing]; то же в domains/commitments.py:197

## Как понять, что готово
на «Голосовом от брата» в trace verify valid, recall 'пропущено: N', gate fail — как было до 73effac; python -m evals.run проходит

## Ответ
⟨заполняет диана: сделано / в работе / отклонено и почему⟩
