# AI Service

Отдельный API-сервис для создания новых курсов, генерации большого `.docx`,
сохранения базовой информации в PostgreSQL и хранения полного JSON-профиля программы.

## Быстрый старт

```powershell
cd C:\Users\rhali\Desktop\contracts2512\ai_service
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

Swagger:

[http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)

## Основные endpoint'ы

- `POST /api/course-drafts/generate`
- `GET /api/course-drafts/{draft_id}`
- `PUT /api/course-drafts/{draft_id}`
- `POST /api/course-drafts/{draft_id}/export-docx`
- `POST /api/course-drafts/{draft_id}/confirm`
- `GET /api/health`

## Папки

- `storage/drafts` — локальные JSON-черновики
- `storage/output` — готовые `.docx`
- `sql/program_profile_json.sql` — таблица для полного JSON-профиля

## Важно

Если `program_template.docx` отсутствует, сервис всё равно сможет собрать документ:
в `v1` включён fallback-режим генерации `.docx` напрямую через `python-docx`.
