from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.db_service import ConfirmedDraftResult
from app.services.document_builder import DocumentBuilder
from app.services.draft_builder import DraftBuilder
from app.services.draft_storage import DraftStorageService
from app.services.validation_service import ValidationService


class FakeOllamaService:
    def check_health(self) -> bool:
        return True

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        if "SECTION_ID:program_goal" in prompt:
            return (
                "Цель реализации программы — подготовка слушателя к практическому применению "
                "современных инструментов и методик по профилю курса."
            )
        if "SECTION_ID:working_programs_block" in prompt:
            return (
                "Модуль 1. Практическое освоение ключевых инструментов.\n"
                "Цель: закрепить базовые и прикладные навыки.\n"
                "Тема 1.1. Введение.\n"
                "Тема 1.2. Практика."
            )
        return "Текстовый блок для документа."

    def generate_json(self, prompt: str, system_prompt: str | None = None) -> dict:
        if "SECTION_ID:module_enrichment" in prompt:
            return {
                "modules": [
                    {
                        "number": 1,
                        "description": "Модуль посвящён освоению фундаментальных практик.",
                        "themes": ["Основы", "Практика", "Контроль результата"],
                    },
                    {
                        "number": 2,
                        "description": "Модуль посвящён прикладным сценариям работы.",
                        "themes": ["Сценарии", "Инструменты", "Итоговое задание"],
                    },
                ]
            }
        if "SECTION_ID:professional_objects" in prompt:
            return {
                "items": [
                    "Программные решения",
                    "Пользовательские сценарии",
                    "Техническая документация",
                    "Практические кейсы",
                ]
            }
        if "SECTION_ID:learning_results" in prompt:
            return {
                "qualification_level": "4 уровень квалификации.",
                "activity_types": [
                    "Разработка и проектирование решений.",
                    "Тестирование и сопровождение результатов.",
                ],
                "final_attestation_result": "По итогам освоения программы слушателю выдаётся документ о квалификации.",
                "standards_basis": "Программа разработана с учётом профильных стандартов и методических материалов.",
                "labor_functions": [
                    {
                        "name": "Анализ требований.",
                        "code_level": "A/01.3",
                        "competencies": ["ПК 1.1. Анализировать требования."],
                    },
                    {
                        "name": "Разработка решений.",
                        "code_level": "A/02.3",
                        "competencies": ["ПК 2.1. Разрабатывать решения."],
                    },
                ],
                "activity_matrix": [
                    {
                        "activity": "Разработка решений.",
                        "competencies": "Профессиональные компетенции: применять профильные методы.",
                        "practical_experience": "Практический опыт: выполнение заданий.",
                        "skills": "Умения: использовать инструменты.",
                        "knowledge": "Знания: понимать принципы и методы.",
                    }
                ],
            }
        if "SECTION_ID:assessment_block" in prompt:
            return {
                "current_control_block": "Текущий контроль включает практические задания.",
                "intermediate_attestation_block": "Промежуточная аттестация проводится в форме зачёта.",
                "final_attestation_intro_block": "Итоговая аттестация подтверждает достижение результатов.",
                "final_attestation_form_and_goals_block": "Форма итоговой аттестации — экзамен.",
                "portfolio_requirements_block": "В портфолио включаются практические работы.",
                "attestation_procedure_block": "Аттестация проводится по утверждённому графику.",
                "report_structure_block": "Доклад включает цель, решения и результаты.",
                "commission_questions_block": "Комиссия задаёт вопросы по содержанию модулей.",
                "results_and_retake_block": "Предусмотрена пересдача в установленный срок.",
                "exam_grading_criteria_block": "Оценка учитывает полноту, корректность и качество оформления.",
            }
        return {}


class FakeDbService:
    def check_health(self) -> bool:
        return True

    def save_confirmed_draft(self, draft, document_path):
        return ConfirmedDraftResult(program_id=101, document_path=str(document_path))


def build_settings(tmp_path: Path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        service_root=tmp_path,
        template_path=tmp_path / "program_template.docx",
        drafts_dir=tmp_path / "drafts",
        output_dir=tmp_path / "output",
    )


@pytest.fixture
def seed_payload() -> dict:
    return {
        "course_name": "Инженерия промптов для команд",
        "program_type": "Программа профессиональной переподготовки",
        "format": "Заочная с применением дистанционных образовательных технологий",
        "hours": 72,
        "target_audience": "лица, имеющие среднее профессиональное и (или) высшее образование;",
        "qualification": "специалист по разработке и внедрению ИИ-решений",
        "professional_area": "Разработка и внедрение решений на базе искусственного интеллекта",
        "training_goal": "Освоение инструментов проектирования, тестирования и внедрения ИИ-сценариев",
        "brief_description": "Курс посвящен созданию промптов, организации процессов проверки результатов и внедрению ИИ в командную работу.",
        "modules_seed": [
            {
                "name": "Основы промпт-инжиниринга",
                "desired_hours": 24,
                "summary": "Базовые паттерны и правила работы с промптами.",
            },
            {
                "name": "Практика внедрения и контроль качества",
                "desired_hours": 24,
                "summary": "Практические сценарии внедрения ИИ и контроль результатов.",
            },
        ],
        "constraints": {
            "standards": ["Внутренний стандарт качества программ ДПО"],
            "required_phrases": ["дистанционные образовательные технологии"],
            "city": "Москва",
            "document_year": 2026,
            "organization_name": "ООО «Центр 25-12»",
            "approval_position": "Генеральный директор",
            "approval_name": "Е. А. Шимбирева",
            "approval_date": "«___» ____________ {{year}} г.",
            "teacher_name": "А.А. Шимбирёв",
            "teacher_position": "Преподаватель высшей квалификационной категории",
            "program_manager_name": "Е.Ю. Бойцова",
            "program_manager_position": "Руководитель направления дополнительного профессионального образования",
        },
        "pricing_meta": {
            "price": "45000",
            "lessons_count": 24,
            "program_view": "ПП",
        },
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    validation = ValidationService(settings)
    fake_ollama = FakeOllamaService()
    overrides = {
        "settings": settings,
        "validation_service": validation,
        "ollama_service": fake_ollama,
        "draft_storage": DraftStorageService(settings, validation),
        "document_builder": DocumentBuilder(settings),
        "db_service": FakeDbService(),
        "draft_builder": DraftBuilder(settings, fake_ollama, validation),
    }
    return TestClient(create_app(service_overrides=overrides, settings=settings))
