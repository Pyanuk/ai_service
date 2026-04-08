from __future__ import annotations

from pathlib import Path
import zipfile

from docx import Document


def test_generate_draft(client, seed_payload):
    response = client.post("/api/course-drafts/generate", json=seed_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"]
    assert body["draft"]["program_card"]["course_name"] == seed_payload["course_name"]
    assert len(body["draft"]["modules"]) == 2


def test_generate_draft_rejects_invalid_payload(client, seed_payload):
    broken = dict(seed_payload)
    broken["modules_seed"] = []
    response = client.post("/api/course-drafts/generate", json=broken)
    assert response.status_code == 422


def test_export_docx(client, seed_payload):
    draft = client.post("/api/course-drafts/generate", json=seed_payload).json()
    draft_id = draft["draft_id"]
    response = client.post(f"/api/course-drafts/{draft_id}/export-docx")
    assert response.status_code == 200
    path = Path(response.json()["document_path"])
    assert path.exists()
    with zipfile.ZipFile(path) as archive:
        text = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "{{" not in text


def test_calendar_variants_follow_pp_load_profile(client, seed_payload):
    body = client.post("/api/course-drafts/generate", json=seed_payload).json()
    descriptions = [variant["description"] for variant in body["draft"]["calendar_variants"]]
    assert len(descriptions) == 5
    assert "3 академических часа в неделю" in descriptions[0]
    assert "включая 2 академических часа взаимодействия с преподавателем" in descriptions[0]
    assert "30 академических часов в неделю" in descriptions[4]
    assert "72 академических часа" in descriptions[0]


def test_calendar_variants_follow_pk_load_profile(client, seed_payload):
    payload = dict(seed_payload)
    payload["program_type"] = "Программа повышения квалификации"
    body = client.post("/api/course-drafts/generate", json=payload).json()
    descriptions = [variant["description"] for variant in body["draft"]["calendar_variants"]]
    assert len(descriptions) == 5
    assert descriptions[0].startswith("Вариант 1")
    assert "3 академических часа в неделю" in descriptions[0]
    assert "12 академических часов в неделю" in descriptions[2]
    assert "самостоятельной работы" in descriptions[0]


def test_calendar_variants_follow_dop_load_profile(client, seed_payload):
    payload = dict(seed_payload)
    payload["program_type"] = "Дополнительная общеобразовательная программа"
    body = client.post("/api/course-drafts/generate", json=payload).json()
    descriptions = [variant["description"] for variant in body["draft"]["calendar_variants"]]
    assert len(descriptions) == 5
    assert "(1 акад. час в неделю)" in descriptions[0]
    assert "(10 акад. часов в неделю)" in descriptions[4]
    assert "самостоятельной работы" not in descriptions[0]


def test_export_docx_layout_regression(client, seed_payload):
    generated = client.post("/api/course-drafts/generate", json=seed_payload).json()
    draft = generated["draft"]
    response = client.post(f"/api/course-drafts/{generated['draft_id']}/export-docx")
    assert response.status_code == 200

    path = Path(response.json()["document_path"])
    document = Document(path)

    blank_count = sum(1 for paragraph in document.paragraphs if not paragraph.text.strip())
    assert blank_count < 250

    working_section_index = next(
        index
        for index, paragraph in enumerate(document.paragraphs)
        if paragraph.text.strip() == "2.4. Рабочие учебные программы дисциплин/модулей"
    )
    assert document.paragraphs[working_section_index + 1].text.startswith("Модуль 1.")
    assert "\n" not in document.paragraphs[working_section_index + 1].text
    assert document.paragraphs[working_section_index + 2].text.startswith("Цель:")
    assert any(paragraph.text == "Содержание:" for paragraph in document.paragraphs[working_section_index + 1 : working_section_index + 20])

    assert len(document.tables[0].rows) == 1 + len(draft["labor_functions"])
    assert len(document.tables[1].rows) == 1 + len(draft["activity_matrix"])
    assert len(document.tables[2].rows) == 3 + len(draft["study_plan"])
    assert all(any(cell.text.strip() for cell in row.cells) for row in document.tables[0].rows[1:])


def test_update_then_export(client, seed_payload):
    draft = client.post("/api/course-drafts/generate", json=seed_payload).json()
    draft_id = draft["draft_id"]
    payload = {
        "updates": {
            "working_programs_block": "Модуль 1. Обновленный блок.\nЦель: Уточнить содержание программы."
        }
    }
    update = client.put(f"/api/course-drafts/{draft_id}", json=payload)
    assert update.status_code == 200
    export = client.post(f"/api/course-drafts/{draft_id}/export-docx")
    assert export.status_code == 200


def test_confirm_saves_program_and_returns_document(client, seed_payload):
    draft = client.post("/api/course-drafts/generate", json=seed_payload).json()
    draft_id = draft["draft_id"]
    response = client.post(f"/api/course-drafts/{draft_id}/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["program_id"] == 101
    assert Path(body["document_path"]).exists()
