from __future__ import annotations

import math
import uuid
from datetime import datetime

from app.config import Settings
from app.schemas.course import CourseSeedRequest
from app.schemas.draft import (
    ActivityMatrixEntry,
    AssessmentBlock,
    CalendarVariant,
    CalendarVariantRow,
    CourseDraft,
    DigitalResourceEntry,
    DocumentMeta,
    FacilityEntry,
    GeneralCharacteristics,
    LaborFunctionEntry,
    ModuleDraft,
    ProgramCard,
    Signatures,
    StudyPlanEntry,
)
from app.services.ollama_service import OllamaService
from app.services.validation_service import ValidationService


class DraftBuilder:
    def __init__(self, settings: Settings, ollama_service: OllamaService, validation_service: ValidationService) -> None:
        self._settings = settings
        self._ollama = ollama_service
        self._validation = validation_service

    def build_draft(self, seed: CourseSeedRequest) -> CourseDraft:
        self._validation.validate_seed(seed)
        modules = self._normalize_modules(seed)
        sections = self._generate_sections(seed, modules)
        study_plan = self._build_study_plan(seed, modules)
        now = datetime.utcnow()

        draft = CourseDraft(
            draft_id=uuid.uuid4().hex,
            seed=seed,
            program_card=ProgramCard(
                course_name=seed.course_name,
                course_name_upper=seed.course_name.upper(),
                program_type=seed.program_type,
                format=seed.format,
                hours=seed.hours,
                target_audience=seed.target_audience,
                qualification=seed.qualification,
                professional_area=seed.professional_area,
                training_goal=seed.training_goal,
                brief_description=seed.brief_description,
                price=seed.pricing_meta.price,
                lessons_count=seed.pricing_meta.lessons_count,
                program_view=seed.pricing_meta.program_view,
                source_url=str(seed.source_url) if seed.source_url else None,
            ),
            general_characteristics=GeneralCharacteristics(
                program_goal=sections["program_goal"],
                professional_area=seed.professional_area,
                professional_objects=sections["professional_objects"],
                activity_types=sections["activity_types"],
                qualification_level=sections["qualification_level"],
                audience_requirements=self._split_items(
                    seed.target_audience,
                    [
                        "Лица, имеющие среднее профессиональное и (или) высшее образование.",
                        "Лица, получающие среднее профессиональное и (или) высшее образование.",
                    ],
                    2,
                ),
                additional_requirements=[
                    "Базовые навыки работы с персональным компьютером, браузером и офисными приложениями.",
                    "Наличие стабильного доступа к интернету и возможности использовать дистанционные образовательные технологии.",
                ],
                entry_requirements="Вступительные испытания не предусмотрены.",
                education_form=seed.format,
                final_attestation_result=sections["final_attestation_result"],
                parallel_education_note=(
                    "Лицам, осваивающим программу параллельно с получением образования, "
                    "документ о квалификации выдаётся одновременно с документом об образовании."
                ),
                standards_basis=sections["standards_basis"],
                calendar_variants_intro_1=(
                    "В целях обеспечения гибкости образовательного процесса предусмотрены различные варианты "
                    "календарного учебного графика, соответствующие объёму программы."
                ),
                calendar_variants_intro_2=(
                    f"Все варианты рассчитаны на общий объём {seed.hours} академических часов "
                    "и обеспечивают достижение планируемых результатов обучения."
                ),
            ),
            labor_functions=sections["labor_functions"],
            activity_matrix=sections["activity_matrix"],
            modules=modules,
            study_plan=study_plan,
            calendar_variants=self._build_calendar_variants(seed, study_plan),
            working_programs_block=sections["working_programs_block"],
            organizational_conditions_block=sections["organizational_conditions_block"],
            assessment_block=sections["assessment_block"],
            signatures=Signatures(
                approval_signature_line="_______________",
                teacher_signature_line="____________________",
                teacher_name=seed.constraints.teacher_name,
                teacher_position=seed.constraints.teacher_position,
                program_manager_signature_line="____________________",
                program_manager_name=seed.constraints.program_manager_name,
                program_manager_position=seed.constraints.program_manager_position,
            ),
            document_meta=DocumentMeta(
                organization_name=seed.constraints.organization_name,
                approval_position=seed.constraints.approval_position,
                approval_name=seed.constraints.approval_name,
                approval_date=seed.constraints.approval_date.replace("{{year}}", str(seed.constraints.document_year)),
                city=seed.constraints.city,
                document_year=seed.constraints.document_year,
                template_name=self._settings.template_path.name,
                created_at=now,
                updated_at=now,
                version=1,
            ),
            facilities=self._build_facilities(seed),
            digital_resources=self._build_resources(seed),
        )
        self._validation.validate_draft(draft)
        return draft

    def _generate_sections(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> dict:
        enrichment = self._prompt_json(
            "module_enrichment",
            f"Курс: {seed.course_name}\nВерни JSON с описанием и темами модулей.",
            default={"modules": []},
        )
        modules_by_number = {
            int(item["number"]): item
            for item in enrichment.get("modules", [])
            if isinstance(item, dict) and "number" in item
        }
        for module in modules:
            item = modules_by_number.get(module.number, {})
            module.description = str(item.get("description", module.summary)).strip() or module.summary
            themes = item.get("themes", [])
            module.theme_titles = self._clean_list(themes, 6) or [
                f"Введение в модуль {module.number}",
                f"Ключевые подходы по теме «{module.name}»",
                f"Практика применения инструментов по теме «{module.name}»",
                f"Итоговое задание по модулю {module.number}",
            ]

        fallback_results = self._fallback_results(seed, modules)
        results = self._prompt_json(
            "learning_results",
            f"Курс: {seed.course_name}\nПрофессиональная область: {seed.professional_area}",
            default={},
        )
        objects = self._prompt_json(
            "professional_objects",
            f"Курс: {seed.course_name}\nПрофессиональная область: {seed.professional_area}",
            default={},
        )
        program_goal = self._normalize_paragraph_block(
            self._prompt_text(
                "program_goal",
                f"Курс: {seed.course_name}\nЦель: {seed.training_goal}\nОписание: {seed.brief_description}",
                fallback=(
                    "Цель реализации программы — формирование у обучающихся профессиональных компетенций, "
                    f"необходимых для деятельности в области {seed.professional_area.lower()}."
                ),
            )
        )
        working_programs_block = self._compose_working_programs_block(
            modules,
            self._prompt_text("working_programs_block", f"Курс: {seed.course_name}", fallback=""),
        )
        organizational = self._normalize_paragraph_block(
            self._prompt_text(
                "organizational_conditions_block",
                f"Курс: {seed.course_name}",
                fallback=self._fallback_organizational_block(),
            )
        )
        assessment = self._prompt_json("assessment_block", f"Курс: {seed.course_name}", default={})

        return {
            "program_goal": program_goal,
            "professional_objects": self._clean_list(objects.get("items", []), 6) or fallback_results["professional_objects"],
            "activity_types": self._clean_list(results.get("activity_types", []), 4) or fallback_results["activity_types"],
            "qualification_level": str(results.get("qualification_level", fallback_results["qualification_level"])).strip(),
            "final_attestation_result": str(results.get("final_attestation_result", fallback_results["final_attestation_result"])).strip(),
            "standards_basis": str(results.get("standards_basis", fallback_results["standards_basis"])).strip(),
            "labor_functions": self._parse_labor_functions(results.get("labor_functions")) or fallback_results["labor_functions"],
            "activity_matrix": self._parse_activity_matrix(results.get("activity_matrix")) or fallback_results["activity_matrix"],
            "working_programs_block": working_programs_block,
            "organizational_conditions_block": organizational,
            "assessment_block": self._normalize_assessment_block(assessment),
        }

    def _normalize_modules(self, seed: CourseSeedRequest) -> list[ModuleDraft]:
        target_total = max(seed.hours - self._attestation_total(seed.hours), 0) or seed.hours
        requested_total = sum(module.desired_hours for module in seed.modules_seed)
        raw = [(module.desired_hours / requested_total) * target_total for module in seed.modules_seed]
        floors = [max(1, math.floor(value)) for value in raw]
        remainder = target_total - sum(floors)
        order = sorted(range(len(raw)), key=lambda idx: raw[idx] - math.floor(raw[idx]), reverse=True)
        for idx in order:
            if remainder <= 0:
                break
            floors[idx] += 1
            remainder -= 1
        return [
            ModuleDraft(
                number=index,
                name=module.name,
                hours=floors[index - 1],
                summary=module.summary,
                description=module.summary,
                theme_titles=[],
            )
            for index, module in enumerate(seed.modules_seed, start=1)
        ]

    def _build_study_plan(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> list[StudyPlanEntry]:
        module_rows: list[StudyPlanEntry] = []
        total_distance = total_lectures = total_practice = total_srs = 0
        for module in modules:
            distance = min(module.hours, max(0, int(round(module.hours * 0.67))))
            lectures = distance // 2
            practice = distance - lectures
            srs = module.hours - distance
            total_distance += distance
            total_lectures += lectures
            total_practice += practice
            total_srs += srs
            module_rows.append(
                StudyPlanEntry(
                    number=str(module.number),
                    name=module.name,
                    total_hours=module.hours,
                    distance_total=distance,
                    lectures=lectures,
                    labs=0,
                    practice=practice,
                    srs=srs,
                    current_control="Выполнение практического задания",
                    intermediate_attestation="Зачёт",
                )
            )

        prep = self._preparation_hours(seed.hours)
        exam = self._final_attestation_hours(seed.hours)
        total_module_hours = sum(m.hours for m in modules)
        return [
            StudyPlanEntry(
                number="№",
                name="Наименование дисциплин (модулей)",
                total_hours=total_module_hours,
                distance_total=total_distance,
                lectures=total_lectures,
                labs=0,
                practice=total_practice,
                srs=total_srs,
            ),
            *module_rows,
            StudyPlanEntry(
                number="",
                name="Подготовка к итоговой аттестации",
                total_hours=prep,
                distance_total=0,
                lectures=0,
                labs=0,
                practice=0,
                srs=prep,
            ),
            StudyPlanEntry(
                number="",
                name="Проведение итоговой аттестации",
                total_hours=exam,
                distance_total=exam,
                lectures=0,
                labs=0,
                practice=exam,
                srs=0,
                intermediate_attestation="Экзамен",
            ),
            StudyPlanEntry(
                number="",
                name="Итого:",
                total_hours=seed.hours,
                distance_total=total_distance + exam,
                lectures=total_lectures,
                labs=0,
                practice=total_practice + exam,
                srs=total_srs + prep,
            ),
        ]

    def _build_calendar_variants(self, seed: CourseSeedRequest, study_plan: list[StudyPlanEntry]) -> list[CalendarVariant]:
        module_rows = [row for row in study_plan if row.number.isdigit()]
        program_category = self._program_category(seed.program_type)
        configs = self._program_load_profiles(program_category)
        variants: list[CalendarVariant] = []

        for title, label, weekly_hours, teacher_hours, self_study_hours in configs:
            rows: list[CalendarVariantRow] = []
            start_week = 1
            for row in module_rows:
                duration = max(1, math.ceil(row.total_hours / weekly_hours))
                end_week = start_week + duration - 1
                rows.append(
                    CalendarVariantRow(
                        period=self._week_range(start_week, end_week),
                        content=row.name,
                        total_hours=row.total_hours,
                        distance_with_teacher=row.distance_total,
                        srs=row.srs,
                        attestation=0,
                        duration_weeks=duration,
                        hours_per_week=weekly_hours,
                        teacher_hours_per_week=teacher_hours,
                    )
                )
                start_week = end_week + 1

            prep = self._preparation_hours(seed.hours)
            exam = self._final_attestation_hours(seed.hours)
            total = prep + exam
            if total:
                duration = max(1, math.ceil(total / weekly_hours))
                rows.append(
                    CalendarVariantRow(
                        period=self._week_range(start_week, start_week + duration - 1),
                        content="Подготовка и проведение итоговой аттестации",
                        total_hours=total,
                        distance_with_teacher=exam,
                        srs=prep,
                        attestation=1,
                        duration_weeks=duration,
                        hours_per_week=max(weekly_hours, math.ceil(total / duration)),
                        teacher_hours_per_week=max(0, math.ceil(exam / duration)),
                    )
                )

            total_weeks = sum(row.duration_weeks for row in rows)
            variants.append(
                CalendarVariant(
                    title=title,
                    description=self._build_calendar_variant_description(
                        program_category=program_category,
                        title=title,
                        label=label,
                        weekly_hours=weekly_hours,
                        teacher_hours=teacher_hours,
                        self_study_hours=self_study_hours,
                        total_weeks=total_weeks,
                        total_hours=seed.hours,
                    ),
                    rows=rows,
                    total_weeks=total_weeks,
                )
            )
        return variants

    def _build_facilities(self, seed: CourseSeedRequest) -> list[FacilityEntry]:
        return [
            FacilityEntry(
                name="Виртуальная образовательная платформа",
                lesson_type="Лекции, практические занятия, самостоятельная работа",
                equipment="LMS-платформа, личные кабинеты слушателей, электронные материалы",
            ),
            FacilityEntry(
                name="Среда видеоконференцсвязи",
                lesson_type="Консультации, вебинары, итоговая аттестация",
                equipment="Сервис видеосвязи, камера, микрофон, демонстрация экрана",
            ),
            FacilityEntry(
                name="Профильные программные средства",
                lesson_type="Практические занятия",
                equipment=f"Набор программных средств и инструментов по тематике курса «{seed.course_name}»",
            ),
        ]

    def _build_resources(self, seed: CourseSeedRequest) -> list[DigitalResourceEntry]:
        return [
            DigitalResourceEntry(
                name="Электронная образовательная среда",
                lesson_type="Доступ к материалам и заданиям",
                equipment="LMS-платформа или её аналог",
            ),
            DigitalResourceEntry(
                name="Облачные сервисы совместной работы",
                lesson_type="Практическая и проектная работа",
                equipment="Общий доступ к файлам, комментарии, история версий",
            ),
            DigitalResourceEntry(
                name="Электронные библиотеки и справочные системы",
                lesson_type="Самостоятельная работа",
                equipment=f"Материалы по направлению «{seed.professional_area}»",
            ),
        ]

    def _prompt_text(self, section_id: str, prompt: str, fallback: str) -> str:
        try:
            return self._ollama.generate_text(f"SECTION_ID:{section_id}\n{prompt}")
        except Exception:
            return fallback

    def _prompt_json(self, section_id: str, prompt: str, default: dict) -> dict:
        try:
            return self._ollama.generate_json(f"SECTION_ID:{section_id}\n{prompt}")
        except Exception:
            return default

    def _compose_working_programs_block(self, modules: list[ModuleDraft], raw_text: str) -> str:
        extra_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        lines: list[str] = []
        for module in modules:
            lines.append(f"Модуль {module.number}. {module.description}")
            lines.append(f"Цель: {module.description}")
            for theme_index, theme in enumerate(module.theme_titles[:6], start=1):
                lines.append(f"Тема {module.number}.{theme_index}. {theme}")
                lines.append("Содержание:")
                lines.extend(self._module_content_lines(module, theme, extra_lines))
        return "\n".join(lines)

    def _module_content_lines(self, module: ModuleDraft, theme: str, extra_lines: list[str]) -> list[str]:
        lines = [
            f"Ключевые понятия и подходы по теме «{theme}».",
            f"Практические сценарии применения решений по теме «{theme}».",
            f"Разбор типовых ошибок и ограничений в модуле «{module.name}».",
        ]
        for line in extra_lines:
            if len(lines) >= 4:
                break
            if line.startswith(("Модуль ", "Цель:", "Тема ")) or line == "Содержание:":
                continue
            if line not in lines:
                lines.append(line)
        return lines[:4]

    def _normalize_paragraph_block(self, text: str) -> str:
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _fallback_organizational_block(self) -> str:
        return (
            "а) Материально-технические условия\n"
            "Для реализации программы требуется компьютер с доступом в интернет, актуальный браузер и профильное программное обеспечение.\n"
            "б) Учебно-методическое и информационное обеспечение\n"
            "Программа реализуется с использованием электронных образовательных ресурсов, методических материалов и практических заданий.\n"
            "в) Кадровые условия\n"
            "К реализации программы привлекаются преподаватели и эксперты, имеющие профильное образование и практический опыт.\n"
            "г) Электронная информационно-образовательная среда\n"
            "Для взаимодействия со слушателями используются дистанционные образовательные платформы, сервисы видеосвязи и электронные библиотеки."
        )

    def _fallback_results(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> dict:
        return {
            "professional_objects": [
                seed.course_name,
                f"Практические модули по направлению {seed.professional_area.lower()}",
                "Пользовательские сценарии и прикладные кейсы",
                "Техническая и проектная документация",
                "Инструменты контроля качества и оценки результатов",
            ],
            "activity_types": [
                f"Проектирование и реализация решений по направлению {seed.professional_area.lower()}",
                f"Применение инструментов и подходов курса «{seed.course_name}» в практической деятельности",
            ],
            "qualification_level": "Уровень квалификации определяется содержанием программы и профилем курса.",
            "final_attestation_result": (
                f"В результате успешного освоения программы слушателю выдаётся документ "
                f"с указанием квалификации: {seed.qualification}."
            ),
            "standards_basis": (
                "Программа разработана с учётом следующих оснований: "
                + (", ".join(seed.constraints.standards) if seed.constraints.standards else "профильных нормативных и методических документов.")
            ),
            "labor_functions": [
                LaborFunctionEntry(
                    name="Анализ требований и формализация задач обучения.",
                    code_level="A/01.3",
                    competencies=["ПК 1.1. Анализировать требования.", "ПК 1.2. Планировать решение задачи."],
                ),
                LaborFunctionEntry(
                    name="Разработка и оформление практических решений.",
                    code_level="A/02.3",
                    competencies=["ПК 2.1. Выполнять практические работы.", "ПК 2.2. Применять профильные инструменты."],
                ),
                LaborFunctionEntry(
                    name="Проверка, корректировка и представление результата.",
                    code_level="A/05.3",
                    competencies=["ПК 3.1. Контролировать качество результата.", "ПК 3.2. Представлять итоговую работу."],
                ),
            ],
            "activity_matrix": [
                ActivityMatrixEntry(
                    activity=f"Практическая деятельность по модулю «{module.name}».",
                    competencies=f"Профессиональные компетенции: применять методы и инструменты по теме «{module.name}».",
                    practical_experience=f"Практический опыт: выполнение заданий и оформление результатов по модулю «{module.name}».",
                    skills=f"Умения: уверенно использовать инструменты и методы по теме «{module.name}».",
                    knowledge=f"Знания: ключевые понятия, подходы и требования по теме «{module.name}».",
                )
                for module in modules[:2]
            ]
            or [
                ActivityMatrixEntry(
                    activity=f"Практическая деятельность по программе «{seed.course_name}».",
                    competencies="Профессиональные компетенции: применять методы и инструменты программы.",
                    practical_experience="Практический опыт: выполнение заданий и оформление результата.",
                    skills="Умения: решать практические задачи по профилю программы.",
                    knowledge="Знания: ключевые понятия, подходы и требования по профилю программы.",
                )
            ],
        }

    def _fallback_assessment(self) -> dict[str, str]:
        return {
            "current_control_block": "Текущий контроль проводится по завершении отдельных тем и модулей в форме практических заданий, тестов и мини-проектов.",
            "intermediate_attestation_block": "Промежуточная аттестация проводится по завершении модулей в форме зачёта или оценки практической работы.",
            "final_attestation_intro_block": "Итоговая аттестация подтверждает готовность слушателя к применению полученных компетенций в профессиональной деятельности.",
            "final_attestation_form_and_goals_block": "Форма итоговой аттестации — итоговый экзамен и (или) защита итоговой практической работы.",
            "portfolio_requirements_block": "В портфолио включаются выполненные практические задания, итоговая работа и подтверждающие материалы.",
            "attestation_procedure_block": "Итоговая аттестация проводится по утверждённому графику и включает проверку материалов, оценку результата и фиксацию решения комиссии.",
            "report_structure_block": "Доклад слушателя включает цель работы, описание выполненных решений, используемые инструменты, результаты и выводы.",
            "commission_questions_block": "Примерные вопросы комиссии охватывают содержание модулей, используемые подходы, применённые инструменты и качество результата.",
            "results_and_retake_block": "Результаты итоговой аттестации оформляются протоколом.\nПри неудовлетворительном результате слушателю предоставляется право пересдачи в установленный срок.",
            "exam_grading_criteria_block": "Оценивание ведётся по нескольким критериям: полнота выполнения задания, корректность решений, самостоятельность, качество оформления результата и способность аргументированно представить выполненную работу.",
        }

    def _normalize_assessment_block(self, assessment: dict) -> AssessmentBlock:
        fallback = self._fallback_assessment()
        normalized: dict[str, str] = {}
        for key, fallback_value in fallback.items():
            value = assessment.get(key)
            normalized[key] = self._normalize_paragraph_block(value) if isinstance(value, str) and value.strip() else fallback_value
        return AssessmentBlock(**normalized)

    def _parse_labor_functions(self, values) -> list[LaborFunctionEntry]:
        if not isinstance(values, list):
            return []
        result: list[LaborFunctionEntry] = []
        for item in values:
            try:
                result.append(
                    LaborFunctionEntry(
                        name=str(item["name"]).strip(),
                        code_level=str(item["code_level"]).strip(),
                        competencies=self._clean_list(item.get("competencies", []), 4),
                    )
                )
            except Exception:
                continue
        return result

    def _parse_activity_matrix(self, values) -> list[ActivityMatrixEntry]:
        if not isinstance(values, list):
            return []
        result: list[ActivityMatrixEntry] = []
        for item in values:
            try:
                result.append(
                    ActivityMatrixEntry(
                        activity=str(item["activity"]).strip(),
                        competencies=str(item["competencies"]).strip(),
                        practical_experience=str(item["practical_experience"]).strip(),
                        skills=str(item["skills"]).strip(),
                        knowledge=str(item["knowledge"]).strip(),
                    )
                )
            except Exception:
                continue
        return result

    def _clean_list(self, values, limit: int) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()][:limit]

    def _split_items(self, raw: str, fallback: list[str], minimum: int) -> list[str]:
        parts = [item.strip() for item in raw.replace("\r", "\n").replace(";", "\n").split("\n") if item.strip()]
        while len(parts) < minimum:
            parts.append(fallback[len(parts)])
        return parts

    def _attestation_total(self, total_hours: int) -> int:
        minimum_total = self._settings.preparation_hours + self._settings.final_attestation_hours
        return 0 if total_hours <= minimum_total else minimum_total

    def _preparation_hours(self, total_hours: int) -> int:
        return self._settings.preparation_hours if self._attestation_total(total_hours) else 0

    def _final_attestation_hours(self, total_hours: int) -> int:
        return self._settings.final_attestation_hours if self._attestation_total(total_hours) else 0

    def _week_range(self, start: int, end: int) -> str:
        return f"{start} нед." if start == end else f"{start}–{end} нед."

    def _program_category(self, program_type: str) -> str:
        normalized = program_type.lower()
        if "повыш" in normalized or "квалификац" in normalized:
            return "pk"
        if "доп" in normalized or "дополнитель" in normalized:
            return "dop"
        return "pp"

    def _program_load_profiles(self, program_category: str) -> list[tuple[str, str, int, int, int]]:
        if program_category == "pk":
            return [
                ("Вариант 1", "С пониженной недельной учебной нагрузкой", 3, 2, 1),
                ("Вариант 2", "С умеренной недельной учебной нагрузкой", 6, 4, 2),
                ("Вариант 3", "Со стандартной недельной учебной нагрузкой", 12, 8, 4),
                ("Вариант 4", "С высокой недельной учебной нагрузкой", 15, 10, 5),
                ("Вариант 5", "С повышенной недельной учебной нагрузкой", 30, 20, 10),
            ]
        if program_category == "dop":
            return [
                ("Вариант 1", "С пониженной недельной учебной нагрузкой", 1, 1, 0),
                ("Вариант 2", "С умеренной недельной учебной нагрузкой", 2, 2, 0),
                ("Вариант 3", "Со стандартной недельной учебной нагрузкой", 4, 4, 0),
                ("Вариант 4", "С высокой недельной учебной нагрузкой", 8, 8, 0),
                ("Вариант 5", "С повышенной недельной учебной нагрузкой", 10, 10, 0),
            ]
        return [
            ("Вариант 1", "С пониженной недельной учебной нагрузкой", 3, 2, 1),
            ("Вариант 2", "С умеренной недельной учебной нагрузкой", 6, 4, 2),
            ("Вариант 3", "Со стандартной недельной учебной нагрузкой", 12, 8, 4),
            ("Вариант 4", "С высокой недельной учебной нагрузкой", 15, 10, 5),
            ("Вариант 5", "С повышенной недельной учебной нагрузкой", 30, 20, 10),
        ]

    def _build_calendar_variant_description(
        self,
        program_category: str,
        title: str,
        label: str,
        weekly_hours: int,
        teacher_hours: int,
        self_study_hours: int,
        total_weeks: int,
        total_hours: int,
    ) -> str:
        if program_category == "dop":
            return (
                f"{title} — {label.lower()} ({self._academic_hours_phrase(weekly_hours, abbreviated=True)} в неделю). "
                f"Общая продолжительность освоения — {total_weeks} {self._plural_form(total_weeks, 'неделя', 'недели', 'недель')}; "
                f"объём программы — {self._academic_hours_phrase(total_hours)}."
            )
        return (
            f"{title} — {label.lower()}. Недельная учебная нагрузка по программе составляет "
            f"{self._academic_hours_phrase(weekly_hours)} в неделю, "
            f"включая {self._academic_hours_phrase(teacher_hours)} взаимодействия с преподавателем "
            f"и {self._academic_hours_phrase(self_study_hours)} самостоятельной работы; "
            f"общая продолжительность освоения — {total_weeks} {self._plural_form(total_weeks, 'неделя', 'недели', 'недель')}. "
            f"Объём программы — {self._academic_hours_phrase(total_hours)}."
        )

    def _plural_form(self, value: int, one: str, few: str, many: str) -> str:
        remainder_100 = value % 100
        remainder_10 = value % 10
        if 11 <= remainder_100 <= 14:
            return many
        if remainder_10 == 1:
            return one
        if 2 <= remainder_10 <= 4:
            return few
        return many

    def _academic_hours_phrase(self, value: int, abbreviated: bool = False) -> str:
        if abbreviated:
            return f"{value} акад. {self._plural_form(value, 'час', 'часа', 'часов')}"
        adjective = "академический" if self._plural_form(value, "one", "few", "many") == "one" else "академических"
        return f"{value} {adjective} {self._plural_form(value, 'час', 'часа', 'часов')}"
