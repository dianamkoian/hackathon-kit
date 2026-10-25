# 012. добавить python-multipart в requirements.txt

**От:** Alexander Rosushchan
**Кому:** диана (зона: core/pipeline.py, core/providers.py, domains/<тема>.py, пороги)
**Статус:** сделана

## Что сделать
добавить python-multipart в requirements.txt

## Зачем
abd6e84 добавил /run-audio с UploadFile/File — FastAPI без python-multipart падает при импорте api.server, и хук перед пушем (smoke) валится у всех, кто ставил окружение по requirements.txt. Воспроизведение: чистое окружение по requirements.txt → git push → 'Form data requires python-multipart'. У Саши поставлено вручную, пуш прошёл

## Как понять, что готово
python-multipart с зафиксированной версией в requirements.txt; smoke проходит в окружении только из requirements.txt

## Ответ
Сделано Дианой в `b6cb094`: `python-multipart~=0.0.28` в `requirements.txt`. Закрывает Соня как временный лид (решение 008), 25.10, 15:16.
