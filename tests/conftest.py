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
        template_path=base.service_root / "правильный пример.docx",
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
def devops_seed_payload() -> dict:
    return {
        "course_name": "Python в DevOps и автоматизация инфраструктуры",
        "program_type": "Программа профессиональной переподготовки",
        "format": "Заочная с применением электронного обучения и дистанционных образовательных технологий, с преподавателем в группе по фиксированному расписанию",
        "hours": 256,
        "target_audience": "лица, имеющие среднее профессиональное и (или) высшее образование; лица, получающие среднее профессиональное и (или) высшее образование;",
        "qualification": "специалист по информационным системам",
        "professional_area": "Автоматизация инфраструктуры, сопровождение и развитие программного обеспечения и информационных систем",
        "training_goal": "Освоение Python в DevOps-практиках, автоматизации инфраструктуры, контейнеризации, оркестрации, CI/CD, мониторинге и управлении конфигурациями",
        "brief_description": "Программа объединяет программирование на Python, инфраструктурную автоматизацию, DevOps-инструменты, контейнеризацию и оркестрацию. Слушатели осваивают построение CI/CD, администрирование через SSH, автоматизацию конфигураций с Ansible, инфраструктуру как код с Terraform, мониторинг и логирование, а также работу с Docker и Kubernetes.",
        "modules_seed": [
            {
                "name": "Программирование на языке Python",
                "desired_hours": 64,
                "summary": "Базовый и прикладной Python для автоматизации, работы с данными, файлами, ошибками, функциями и скриптами администрирования.",
            },
            {
                "name": "Python для DevOps",
                "desired_hours": 64,
                "summary": "Применение Python в DevOps: SSH, Linux-автоматизация, управление пользователями, Ansible, Terraform, мониторинг, логирование и основы CI/CD.",
            },
            {
                "name": "Работа с Docker и Kubernetes",
                "desired_hours": 64,
                "summary": "Контейнеризация, Docker, Docker Compose, Kubernetes, Minikube, деплой, конфигурации, секреты, балансировка нагрузки и масштабирование.",
            },
            {
                "name": "Автоматизация DevOps-процессов на Python",
                "desired_hours": 64,
                "summary": "Автоматизация инфраструктурных и DevOps-процессов на Python: мониторинг, логирование, CI/CD, управление облачными сервисами, Kubernetes, тестирование и анализ уязвимостей.",
            },
        ],
        "constraints": {
            "standard_profile_id": "fgos_spo_09_02_11",
            "standard_track_id": "devops_infrastructure",
            "standards": [
                "Профессиональный стандарт 06.015 «Специалист по информационным системам»",
                "ФГОС СПО 09.02.11 «Разработка и управление программным обеспечением»",
            ],
            "required_phrases": [
                "дистанционные образовательные технологии",
                "инфраструктура как код",
                "контейнеризация",
                "CI/CD",
                "автоматизация инфраструктуры",
            ],
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
            "price": "144000",
            "lessons_count": 144,
            "program_view": "ПП",
        },
        "source_url": "https://25-12.ru/courses/python-%D0%B2-devops-%D0%B8-%D0%B0%D0%B2%D1%82%D0%BE%D0%BC%D0%B0%D1%82%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D1%8F-%D0%B8%D0%BD%D1%84%D1%80%D0%B0%D1%81%D1%82%D1%80%D1%83%D0%BA%D1%82%D1%83%D1%80%D1%8B-%D1%81/",
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
