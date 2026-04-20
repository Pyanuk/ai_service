from __future__ import annotations

import html
import math
import re
import uuid
from datetime import datetime
import requests

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
from app.services.standard_profiles import ResolvedStandardProfile, resolve_standard_profile
from app.services.validation_service import ValidationService


class DraftBuilder:
    def __init__(self, settings: Settings, ollama_service: OllamaService, validation_service: ValidationService) -> None:
        self._settings = settings
        self._ollama = ollama_service
        self._validation = validation_service
        self._source_outline_cache: dict[str, dict[str, list[tuple[str, str]]]] = {}

    def build_draft(self, seed: CourseSeedRequest) -> CourseDraft:
        self._validation.validate_seed(seed)
        profile = resolve_standard_profile(seed)
        seed = seed.model_copy(
            update={
                "constraints": seed.constraints.model_copy(
                    update={
                        "standard_profile_id": profile.profile_id,
                        "standard_track_id": profile.track_id,
                    }
                )
            }
        )
        modules = self._normalize_modules(seed)
        sections = self._generate_sections(seed, modules, profile)
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
                professional_area=profile.professional_area,
                professional_objects=sections["professional_objects"],
                activity_types=sections["activity_types"],
                qualification_level=sections["qualification_level"],
                audience_requirements=list(profile.audience_requirements),
                additional_requirements=list(profile.additional_requirements),
                entry_requirements=profile.entry_requirements,
                education_form=seed.format,
                final_attestation_result=sections["final_attestation_result"],
                parallel_education_note=profile.parallel_education_note,
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

    def _generate_sections(
        self,
        seed: CourseSeedRequest,
        modules: list[ModuleDraft],
        profile: ResolvedStandardProfile,
    ) -> dict:
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
            exact_theme_titles = self._exact_theme_titles(seed, module)
            if exact_theme_titles:
                module.theme_titles = exact_theme_titles
            else:
                themes = item.get("themes", [])
                module.theme_titles = self._compose_theme_titles(module.name, module.summary, themes)

        working_programs_block = self._normalize_working_programs_block(self._compose_working_programs_block(
            seed,
            modules,
            self._prompt_text("working_programs_block", f"Курс: {seed.course_name}", fallback=""),
        ))
        organizational = self._normalize_paragraph_block(self._build_organizational_conditions_block(seed))
        return {
            "program_goal": self._normalize_paragraph_block(profile.program_goal),
            "professional_objects": list(profile.professional_objects),
            "activity_types": list(profile.activity_types),
            "qualification_level": profile.qualification_level,
            "final_attestation_result": profile.final_attestation_result,
            "standards_basis": profile.standards_basis,
            "labor_functions": list(profile.labor_functions),
            "activity_matrix": self._expand_activity_matrix(profile),
            "working_programs_block": working_programs_block,
            "organizational_conditions_block": organizational,
            "assessment_block": self._build_assessment_block(seed, modules),
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
        exact = self._exact_facilities(seed)
        if exact:
            return exact
        return self._catalog_facilities(seed)

    def _build_resources(self, seed: CourseSeedRequest) -> list[DigitalResourceEntry]:
        exact = self._exact_digital_resources(seed)
        if exact:
            return exact
        return self._catalog_digital_resources(seed)

    def _build_organizational_conditions_block(self, seed: CourseSeedRequest) -> str:
        lines: list[str] = [
            "а) Материально-технические условия",
            *self._technical_requirements_lines(seed),
            "",
            "б) Учебно-методическое и информационное обеспечение",
            "Программа реализуется исключительно с использованием электронных образовательных ресурсов.",
            "Официальная документация:",
            *self._official_documentation_lines(seed),
            "Электронные учебные пособия:",
            *self._znanium_literature_lines(seed),
            "в) Кадровые условия",
            "Программа реализуется квалифицированными преподавателями, соответствующими требованиям профессиональных стандартов.",
            "Квалификационные требования к преподавателям:",
            "Высшее или среднее профессиональное образование в области информационных технологий, программирования или смежных направлений.",
            "Дополнительное педагогическое образование (повышение квалификации или переподготовка в области преподавания).",
            "Обязательное повышение квалификации не реже одного раза в три года, включая стажировки в IT-компаниях и научных организациях.",
            "г) Условия для функционирования электронной информационно-образовательной среды",
            *self._eios_capability_lines(seed),
        ]
        return "\n".join(lines)

    def _course_haystack(self, seed: CourseSeedRequest) -> str:
        return " ".join(
            [
                seed.course_name,
                seed.professional_area,
                seed.training_goal,
                seed.brief_description,
                " ".join(module.name for module in seed.modules_seed),
                " ".join(module.summary for module in seed.modules_seed),
                " ".join(seed.constraints.required_phrases),
                str(seed.source_url or ""),
            ]
        ).casefold()

    def _course_tags(self, seed: CourseSeedRequest) -> set[str]:
        haystack = self._course_haystack(seed)
        tags: set[str] = set()

        def has(*patterns: str) -> bool:
            return any(pattern in haystack for pattern in patterns)

        if has("python", "pandas", "numpy", "jupyter"):
            tags.add("python")
        if has("javascript", "java script", "ecmascript"):
            tags.add("javascript")
        if has("react", "jsx"):
            tags.update({"react", "frontend", "javascript"})
        if has("frontend", "html", "css", "spa", "веб-интерфейс", "web-интерфейс"):
            tags.add("frontend")
        if has("backend", "server-side", "серверн", "rest api", "graphql"):
            tags.add("backend")
        if has("node.js", "nodejs", "node js"):
            tags.update({"node", "backend", "javascript"})
        if has("express"):
            tags.update({"express", "backend", "javascript"})
        if has("django"):
            tags.update({"django", "python", "backend"})
        if has("django rest framework", "drf"):
            tags.update({"drf", "django", "python", "api"})
        if has("postgresql", "postgres", "sqlalchemy"):
            tags.update({"postgresql", "databases"})
        if has("mongodb", "mongo db"):
            tags.update({"mongodb", "databases"})
        if has("sql", "баз данн", "субд", "orm"):
            tags.add("databases")
        if has("docker", "контейнер", "container"):
            tags.update({"docker", "containers"})
        if has("kubernetes", "k8s", "оркестрац"):
            tags.update({"kubernetes", "containers"})
        if has("devops", "ci/cd", "terraform", "ansible", "мониторинг", "prometheus", "grafana", "инфраструктур"):
            tags.add("devops")
        if has("selenium", "pytest", "playwright", "автоматизированное тестирование", "тестирован", "allure", "jmeter", "postman"):
            tags.add("testing")
        if has("api", "rest", "swagger", "openapi"):
            tags.add("api")
        if has("компьютерн", "сет", "маршрутиз", "коммутац", "tcp/ip", "router", "switch", "wireshark", "cisco", "mikrotik", "vlan", "dhcp", "dns"):
            tags.add("networks")
        if has("linux", "ubuntu", "bash", "ssh"):
            tags.add("linux")
        if has("data science", "анализ данных", "машинн", "искусственн", "ml", "ai", "нейрон"):
            tags.add("data")

        if tags & {"python", "javascript", "frontend", "backend", "devops", "testing", "data", "networks"}:
            tags.add("software")
        if not tags:
            tags.update({"generic", "software"})
        return tags

    def _technical_requirements_lines(self, seed: CourseSeedRequest) -> list[str]:
        tags = self._course_tags(seed)
        if tags & {"devops", "containers", "data"}:
            memory = "8 ГБ ОЗУ"
            cpu = "Intel Core i5"
        else:
            memory = "4 ГБ ОЗУ"
            cpu = "Intel Core i3"
        return [
            "Требования к техническому обеспечению слушателя:",
            f"Компьютер с доступом в интернет (рекомендуется операционная система Windows 10 и выше, Linux, macOS; минимальные характеристики: {memory}, процессор не ниже {cpu}).",
            "Интернет-соединение: стабильный канал со скоростью не менее 5 Мбит/с (рекомендуется 10 Мбит/с и выше для комфортного просмотра вебинаров и работы с онлайн-ресурсами).",
            "Браузер: Google Chrome, Mozilla Firefox, Яндекс.Браузер, Microsoft Edge (последние версии).",
            "Дополнительное ПО: PDF-ридеры (Adobe Acrobat Reader, Okular), архиваторы (7-Zip, WinRAR).",
        ]

    def _official_documentation_lines(self, seed: CourseSeedRequest) -> list[str]:
        tags = self._course_tags(seed)
        lines: list[str] = []

        def add(line: str) -> None:
            if line not in lines:
                lines.append(line)

        if "python" in tags:
            add("Python 3 — официальная документация. Применение: базовый язык программирования, автоматизация и прикладная разработка. Ссылка: https://docs.python.org/3/")
        if "django" in tags:
            add("Django — официальная документация. Применение: разработка серверной логики, моделей, представлений и административных интерфейсов. Ссылка: https://docs.djangoproject.com/")
        if "drf" in tags or ("api" in tags and "django" in tags):
            add("Django REST framework — официальная документация. Применение: проектирование и реализация REST API. Ссылка: https://www.django-rest-framework.org/")
        if "javascript" in tags:
            add("JavaScript (MDN Web Docs) — официальная документация. Применение: синтаксис языка, работа с DOM, модулями, асинхронностью и браузерным API. Ссылка: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide")
        if "frontend" in tags:
            add("HTML и CSS (MDN Web Docs) — официальная документация. Применение: верстка интерфейсов, стилизация, адаптивность и базовая работа с формами. Ссылка: https://developer.mozilla.org/en-US/docs/Learn_web_development")
        if "react" in tags:
            add("React — официальная документация. Применение: разработка компонентных интерфейсов, управление состоянием и маршрутизация SPA-приложений. Ссылка: https://react.dev/")
        if "node" in tags:
            add("Node.js — официальная документация. Применение: серверная разработка на JavaScript, работа с модулями, пакетами и HTTP-сервисами. Ссылка: https://nodejs.org/docs/latest/api/")
        if "express" in tags:
            add("Express — официальная документация. Применение: маршрутизация, middleware, REST API и серверная логика на Node.js. Ссылка: https://expressjs.com/")
        if "postgresql" in tags:
            add("PostgreSQL — официальная документация. Применение: проектирование схем данных, SQL-запросы, индексы и администрирование реляционных баз данных. Ссылка: https://www.postgresql.org/docs/")
        if "mongodb" in tags:
            add("MongoDB — официальная документация. Применение: работа с документными базами данных, коллекциями, индексами и агрегациями. Ссылка: https://www.mongodb.com/docs/")
        if "docker" in tags:
            add("Docker — официальная документация. Применение: контейнеризация приложений, сборка образов и управление контейнерами. Ссылка: https://docs.docker.com/")
        if "kubernetes" in tags:
            add("Kubernetes — официальная документация. Применение: оркестрация контейнеров, deployment, service, ingress и масштабирование. Ссылка: https://kubernetes.io/docs/")
        if "devops" in tags:
            add("GitHub Actions — официальная документация. Применение: настройка CI/CD-конвейеров, автоматизация тестирования и сборки. Ссылка: https://docs.github.com/en/actions")
            add("Terraform — официальная документация. Применение: описание инфраструктуры как кода и автоматизация развертывания. Ссылка: https://developer.hashicorp.com/terraform/docs")
            add("Ansible — официальная документация. Применение: управление конфигурациями и автоматизация типовых операций сопровождения. Ссылка: https://docs.ansible.com/")
            add("Prometheus — официальная документация. Применение: сбор метрик, мониторинг сервисов и настройка алертов. Ссылка: https://prometheus.io/docs/")
            add("Grafana — официальная документация. Применение: визуализация метрик, построение дашбордов и анализ инфраструктуры. Ссылка: https://grafana.com/docs/")
        if "testing" in tags:
            add("pytest — официальная документация. Применение: модульное и интеграционное тестирование, фикстуры, параметризация и отчеты. Ссылка: https://docs.pytest.org/")
            add("Selenium — официальная документация. Применение: автоматизация браузерного тестирования и работа с WebDriver. Ссылка: https://www.selenium.dev/documentation/")
            add("Postman — официальная документация. Применение: тестирование API, коллекции запросов и автоматизированные проверки. Ссылка: https://learning.postman.com/docs/")
        if "networks" in tags:
            add("Cisco Packet Tracer / Cisco Networking Academy — официальная документация. Применение: моделирование сетевой инфраструктуры, маршрутизация, коммутация и лабораторные практикумы. Ссылка: https://www.netacad.com/courses/packet-tracer")
            add("MikroTik RouterOS — официальная документация. Применение: настройка маршрутизаторов, VLAN, VPN, фильтрация трафика и администрирование сетевых сервисов. Ссылка: https://help.mikrotik.com/docs/")
            add("Wireshark User’s Guide — официальная документация. Применение: анализ сетевого трафика, диагностика протоколов и разбор сетевых инцидентов. Ссылка: https://www.wireshark.org/docs/")
            add("Nmap Reference Guide — официальная документация. Применение: сетевое сканирование, аудит доступности хостов и диагностика сервисов. Ссылка: https://nmap.org/book/man.html")
            add("Zabbix — официальная документация. Применение: мониторинг сетевой инфраструктуры, хостов, триггеров и оповещений. Ссылка: https://www.zabbix.com/documentation/current/en/manual")

        return lines[:8]

    def _znanium_literature_lines(self, seed: CourseSeedRequest) -> list[str]:
        tags = self._course_tags(seed)
        lines: list[str] = []

        def add(line: str) -> None:
            if line not in lines:
                lines.append(line)

        if "python" in tags:
            add("Гуриков, С. Р. Основы алгоритмизации и программирования на Python : учебное пособие / С. Р. Гуриков. — Москва : ИНФРА-М, 2025. — 343 с. — ISBN 978-5-16-020255-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2166199 — Режим доступа: по подписке.")
            add("Майтак, Р. В. Python, Django, Data Science : учебное пособие / Р. В. Майтак, П. А. Пылов, А. В. Протодьяконов. — Москва ; Вологда : Инфра-Инженерия, 2025. — 516 с. — ISBN 978-5-9729-2143-0. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2225323 — Режим доступа: по подписке.")
            add("Маккинни, У. Python и анализ данных. Первичная обработка данных с применением pandas, NumPy и Jupyter / У. Маккинни ; пер. А. А. Слинкина. — 3-е изд. — Москва : ДМК Пресс, 2023. — 537 с. — ISBN 978-5-93700-174-0. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2150530 — Режим доступа: по подписке.")
        if "backend" in tags and "javascript" in tags:
            add("Хэррон, Д. Node.js. Разработка серверных веб-приложений на JavaScript : практическое руководство / Д. Хэррон ; пер. с англ. А. А. Слинкина. — 2-е изд. — Москва : ДМК Пресс, 2023. — 145 с. — ISBN 978-5-89818-632-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2108525 — Режим доступа: по подписке.")
            add("Солодушкин, С. И. Разработка программных комплексов на языке JavaScript : учебное пособие / С. И. Солодушкин, И. Ф. Юманова ; под общ. ред. В. Г. Пименова. — Екатеринбург : Изд-во Уральского ун-та, 2020. — 132 с. — ISBN 978-5-7996-3034-8. — Текст : электронный. — URL: https://znanium.ru/catalog/product/1936353 — Режим доступа: по подписке.")
            add("Мартишин, С. А. Базы данных: работа с распределенными базами данных и файловыми системами на примере MongoDB и HDFS с использованием Node.js, Express.js, Apache Spark и Scala : учебное пособие / С. А. Мартишин, В. Л. Симонов, М. В. Храпченко. — Москва : ИНФРА-М, 2023. — 235 с. — ISBN 978-5-16-015643-9. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2111334 — Режим доступа: по подписке.")
        if "frontend" in tags:
            add("Хортон, А. Разработка веб-приложений в ReactJS : практическое руководство / А. Хортон, Р. Вайс ; пер. с англ. Р. Н. Рагимова. — 2-е изд. — Москва : ДМК Пресс, 2023. — 255 с. — ISBN 978-5-89818-503-9. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2107174 — Режим доступа: по подписке.")
            add("Богданов, М. Р. Разработка клиентских приложений Web-сайтов : краткий курс / М. Р. Богданов. — Москва : ИНТУИТ, 2016. — 195 с. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2157481 — Режим доступа: по подписке.")
            add("Новиков, В. А. Web-программирование : учебное пособие / В. А. Новиков. — Минск : Адукацыя i выхаванне, 2024. — 353 с. — ISBN 978-985-599-952-3. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2219541 — Режим доступа: по подписке.")
        if "networks" in tags:
            add("Кузин, А. В. Компьютерные сети : учебное пособие / А. В. Кузин, Д. А. Кузин. — 4-е изд., перераб. и доп. — Москва : ИНФРА-М, 2026. — 190 с. — ISBN 978-5-16-021609-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2232332 — Режим доступа: по подписке.")
            add("Максимов, Н. В. Компьютерные сети : учебное пособие / Н. В. Максимов, И. И. Попов. — 6-е изд., перераб. и доп. — Москва : ИНФРА-М, 2026. — 464 с. — ISBN 978-5-16-021612-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2212373 — Режим доступа: по подписке.")
            add("Баранчиков, А. И. Организация сетевого администрирования : учебник / А. И. Баранчиков, П. А. Баранчиков, А. Ю. Громов, О. А. Ломтева. — Москва : КУРС, 2026. — 384 с. — ISBN 978-5-906818-34-8. — Текст : электронный. — URL: https://znanium.ru/catalog/document?id=472320 — Режим доступа: по подписке.")
        if "testing" in tags and "python" in tags:
            add("Гуриков, С. Р. Основы алгоритмизации и программирования на Python : учебное пособие / С. Р. Гуриков. — Москва : ИНФРА-М, 2025. — 343 с. — ISBN 978-5-16-020255-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2166199 — Режим доступа: по подписке.")

        if not lines:
            add("Кузин, А. В. Компьютерные сети : учебное пособие / А. В. Кузин, Д. А. Кузин. — 4-е изд., перераб. и доп. — Москва : ИНФРА-М, 2026. — 190 с. — ISBN 978-5-16-021609-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2232332 — Режим доступа: по подписке.")
            add("Гуриков, С. Р. Основы алгоритмизации и программирования на Python : учебное пособие / С. Р. Гуриков. — Москва : ИНФРА-М, 2025. — 343 с. — ISBN 978-5-16-020255-6. — Текст : электронный. — URL: https://znanium.ru/catalog/product/2166199 — Режим доступа: по подписке.")

        return lines[:4]

    def _catalog_facilities(self, seed: CourseSeedRequest) -> list[FacilityEntry]:
        tags = self._course_tags(seed)
        rows: list[FacilityEntry] = [
            FacilityEntry(
                name="Виртуальная образовательная платформа",
                lesson_type="Лекции, практические занятия, тестирование, самостоятельная работа",
                equipment="Moodle (основная платформа)",
            ),
            FacilityEntry(
                name="Видеоконференц-связь",
                lesson_type="Вебинары, консультации, защита проектов",
                equipment="Jitsi Meet, ВК Звонки, Яндекс.Телемост",
            ),
        ]

        if "networks" in tags:
            rows.extend(
                [
                    FacilityEntry(
                        name="Среды моделирования и администрирования сетей",
                        lesson_type="Практические занятия",
                        equipment="Cisco Packet Tracer, GNS3, EVE-NG Community, MikroTik WinBox",
                    ),
                    FacilityEntry(
                        name="Средства анализа сетевого трафика",
                        lesson_type="Практические занятия, диагностика",
                        equipment="Wireshark, tcpdump, Nmap",
                    ),
                    FacilityEntry(
                        name="Средства мониторинга сети",
                        lesson_type="Практические занятия, анализ инфраструктуры",
                        equipment="Zabbix, Grafana, PRTG Network Monitor (trial)",
                    ),
                ]
            )
        else:
            rows.append(
                FacilityEntry(
                    name="Среды разработки",
                    lesson_type="Практические занятия",
                    equipment=self._development_environment_equipment(seed),
                )
            )
            rows.append(
                FacilityEntry(
                    name="Онлайн-компиляторы и среды выполнения кода",
                    lesson_type="Практические занятия, отладка кода",
                    equipment=self._online_practice_equipment(seed),
                )
            )
            rows.append(
                FacilityEntry(
                    name="Системы контроля версий",
                    lesson_type="Практические занятия, командные проекты",
                    equipment="GitHub, GitLab (бесплатные версии)",
                )
            )

        if tags & {"testing", "backend", "frontend", "python", "javascript"}:
            rows.append(
                FacilityEntry(
                    name="Системы управления тестированием и дефектами",
                    lesson_type="Практические занятия, командные проекты",
                    equipment="Redmine, Taiga, MantisBT, GitHub Issues, GitHub Projects",
                )
            )
        if "testing" in tags:
            rows.append(
                FacilityEntry(
                    name="Средства тестирования и автоматизации",
                    lesson_type="Практические занятия",
                    equipment="Selenium WebDriver, pytest, Allure, Locust, Apache JMeter",
                )
            )
            rows.append(
                FacilityEntry(
                    name="Инструменты для API-тестирования",
                    lesson_type="Практические занятия",
                    equipment="Postman (free), Insomnia, curl, HTTPie, Bruno",
                )
            )
        if tags & {"docker", "containers", "devops"}:
            rows.append(
                FacilityEntry(
                    name="Средства контейнеризации",
                    lesson_type="Практические занятия, проекты",
                    equipment="Docker Engine (бесплатная версия)",
                )
            )
        if tags & {"kubernetes", "devops"}:
            rows.append(
                FacilityEntry(
                    name="Системы оркестрации контейнеров",
                    lesson_type="Практические занятия, развёртывание инфраструктуры",
                    equipment="Kubernetes (minikube, k3s)",
                )
            )
            rows.append(
                FacilityEntry(
                    name="Инструменты DevOps / CI/CD",
                    lesson_type="Практические занятия, CI/CD-процессы",
                    equipment="Jenkins, GitHub Actions, GitLab CI, Ansible, Terraform",
                )
            )
            rows.append(
                FacilityEntry(
                    name="Средства мониторинга и логирования",
                    lesson_type="Практические занятия, анализ инфраструктуры",
                    equipment="Prometheus, Grafana, ELK-стек (или OpenSearch + OpenSearch Dashboards), Loki + Grafana",
                )
            )

        return rows

    def _catalog_digital_resources(self, seed: CourseSeedRequest) -> list[DigitalResourceEntry]:
        tags = self._course_tags(seed)
        resources = [
            DigitalResourceEntry(
                name="LMS-платформа (Moodle)",
                lesson_type="Лекции, тестирование, выполнение домашних заданий",
                equipment="Moodle (или аналоги)",
            ),
            DigitalResourceEntry(
                name="Видеоконференц-связь",
                lesson_type="Вебинары, консультации, защита проектов",
                equipment="TrueConf, ВК Звонки, Яндекс.Телемост (или аналоги)",
            ),
            DigitalResourceEntry(
                name="Среды программирования",
                lesson_type="Практические занятия, тестирование кода",
                equipment=self._development_environment_equipment(seed),
            ),
            DigitalResourceEntry(
                name="Онлайн-компиляторы",
                lesson_type="Отладка кода, выполнение заданий",
                equipment=self._online_practice_equipment(seed),
            ),
        ]

        if "networks" in tags:
            resources.append(
                DigitalResourceEntry(
                    name="Сетевые тренажеры и лаборатории",
                    lesson_type="Практические занятия, моделирование сети",
                    equipment="Cisco Networking Academy, Packet Tracer Labs, GNS3 Community",
                )
            )
        else:
            resources.append(
                DigitalResourceEntry(
                    name="Хранилища кода",
                    lesson_type="Проектная работа, контроль версий",
                    equipment="GitHub, GitLab",
                )
            )
            resources.append(
                DigitalResourceEntry(
                    name="Интерактивные тренажеры",
                    lesson_type="Практическое обучение",
                    equipment="Stepik, SoloLearn, Kaggle Notebooks",
                )
            )

        resources.append(
            DigitalResourceEntry(
                name="Системы тестирования",
                lesson_type="Контроль знаний",
                equipment="Встроенные тесты Moodle (или аналоги)",
            )
        )
        return resources

    def _development_environment_equipment(self, seed: CourseSeedRequest) -> str:
        tags = self._course_tags(seed)
        if "networks" in tags:
            return "Cisco Packet Tracer, GNS3, EVE-NG Community, Wireshark, MikroTik WinBox"
        if "frontend" in tags and "react" in tags:
            return "Node.js LTS, Visual Studio Code, npm, Vite, React Developer Tools"
        if "backend" in tags and "javascript" in tags:
            return "Node.js LTS, Visual Studio Code, npm, Express Generator, Postman"
        if "python" in tags:
            return "Python 3.13.2, Jupyter Notebook, Visual Studio Code, PyCharm Community Edition"
        return "Visual Studio Code, Git, браузерные DevTools, профильное ПО по тематике курса"

    def _online_practice_equipment(self, seed: CourseSeedRequest) -> str:
        tags = self._course_tags(seed)
        if "networks" in tags:
            return "Cisco Networking Academy Labs, GNS3 Community, Packet Tracer"
        if "frontend" in tags:
            return "StackBlitz, CodeSandbox, Replit"
        if "backend" in tags and "javascript" in tags:
            return "Replit, StackBlitz, Render (free tier), Railway (trial)"
        if "python" in tags:
            return "Google Colab, Replit, JupyterHub"
        return "Replit, GitHub Codespaces (trial), облачные среды выполнения"

    def _eios_capability_lines(self, seed: CourseSeedRequest) -> list[str]:
        tags = self._course_tags(seed)
        lines = [
            "Основные функциональные возможности платформы Moodle и ее аналогов:",
            "Доступ к электронным учебным материалам (лекции, видеоуроки, методички).",
            "Система тестирования (автоматическая проверка, контроль времени выполнения).",
            "Форумы и чаты для взаимодействия с преподавателем.",
            "Система контроля посещаемости и выполнения заданий.",
        ]
        if "networks" in tags:
            lines.append("Размещение схем, конфигураций и файлов лабораторных работ по сетевому администрированию.")
        return lines

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

    def _compose_working_programs_block(self, seed: CourseSeedRequest, modules: list[ModuleDraft], raw_text: str) -> str:
        extra_lines: list[str] = []
        lines: list[str] = []
        for module in modules:
            if lines:
                lines.append("")
            lines.append(f"Модуль {module.number}. {module.name}")
            lines.append(f"Цель: {module.description}")
            for theme_index, (theme, practice) in enumerate(self._working_program_pairs(seed, module), start=1):
                lines.append("")
                lines.append(f"Тема {module.number}.{theme_index}. {theme}")
                lines.append("Содержание:")
                lines.extend(self._module_content_lines(module, theme, extra_lines))
                lines.append("Перечень практических работ занятий:")
                lines.append(f"• {practice}")
                lines.append("Виды самостоятельной работы слушателей (СРС):")
                lines.extend(self._module_self_study_lines(module, theme))
                lines.append("Форма текущего контроля: Выполнение практического задания.")
                lines.append("")
            lines.append("Форма промежуточной аттестации: зачёт.")
            lines.append(
                f"Зачёт по модулю {module.number} проводится в форме тестирования и оценки результатов выполнения практических работ по темам модуля."
            )
            lines.append("Критерии оценки:")
            lines.append("• корректность выполнения практических заданий;")
            lines.append("• понимание основных терминов, инструментов и команд по темам модуля;")
            lines.append("• способность объяснить выбранное решение и результаты его применения.")
            lines.append("Пример тестовых вопросов:")
            lines.extend(self._module_exam_questions(seed, module))
            lines.append("")
        return "\n".join(lines)

    def _module_content_lines(self, module: ModuleDraft, theme: str, extra_lines: list[str]) -> list[str]:
        lines = [f"• {item}" for item in self._topic_content_points(module, theme)]
        for line in extra_lines:
            if len(lines) >= 5:
                break
            if line.startswith(("Модуль ", "Цель:", "Тема ")) or line == "Содержание:":
                continue
            bullet_line = f"• {line.lstrip('•- ')}"
            if bullet_line not in lines:
                lines.append(bullet_line)
        return lines[:5]

    def _module_self_study_lines(self, module: ModuleDraft, theme: str) -> list[str]:
        return [f"• {item}" for item in self._topic_self_study_points(module, theme)]

    def _module_exam_questions(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[str]:
        exact = self._exact_module_exam_questions(seed, module)
        if exact:
            return exact
        return self._catalog_module_exam_questions(seed, module)

    def _normalize_paragraph_block(self, text: str) -> str:
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _expand_activity_matrix(self, profile: ResolvedStandardProfile) -> list[ActivityMatrixEntry]:
        entries = list(profile.activity_matrix)
        if not entries:
            return []

        competency_lines: list[str] = []
        labor_function_lines: list[str] = []
        for labor_function in profile.labor_functions:
            for competency in labor_function.competencies:
                if competency not in competency_lines:
                    competency_lines.append(competency)
            labor_line = f"{labor_function.code_level} {labor_function.name}"
            if labor_line not in labor_function_lines:
                labor_function_lines.append(labor_line)

        first_entry = entries[0]
        entries[0] = first_entry.model_copy(
            update={
                "competencies": self._merge_activity_matrix_competencies(
                    first_entry.competencies,
                    competency_lines,
                    labor_function_lines,
                )
            }
        )
        return entries

    def _merge_activity_matrix_competencies(
        self,
        current_value: str,
        competency_lines: list[str],
        labor_function_lines: list[str],
    ) -> str:
        current_competencies: list[str] = []
        current_labor_functions: list[str] = []
        section = "competencies"

        for raw_line in current_value.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "Профессиональные компетенции:":
                section = "competencies"
                continue
            if line == "Трудовые функции:":
                section = "labor_functions"
                continue
            if section == "competencies":
                current_competencies.append(line)
            else:
                current_labor_functions.append(line)

        merged_competencies: list[str] = []
        for line in [*competency_lines, *current_competencies]:
            if line not in merged_competencies:
                merged_competencies.append(line)

        merged_labor_functions: list[str] = []
        for line in [*labor_function_lines, *current_labor_functions]:
            if line not in merged_labor_functions:
                merged_labor_functions.append(line)

        lines = ["Профессиональные компетенции:", *merged_competencies]
        if merged_labor_functions:
            lines.extend(["Трудовые функции:", *merged_labor_functions])
        return "\n".join(lines)

    def _normalize_working_programs_block(self, text: str) -> str:
        cleaned = text.replace("вЂў", "•")
        blacklist = (
            "конечно",
            "я готов помочь",
            "я могу помочь",
            "в этой секции",
            "вот несколько тем",
            "можно рассмотреть",
            "мы можем обсудить",
            "готов помочь вам",
        )
        lines = [line.strip() for line in cleaned.splitlines()]
        normalized: list[str] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            lowered = line.lower()
            if any(marker in lowered for marker in blacklist):
                index += 1
                continue

            if line.startswith("Форма промежуточной аттестации:"):
                normalized.append(line)
                normalized.append(
                    "Зачет проводится в форме тестирования. Слушатель проходит тест из 30 вопросов по всем темам модуля. "
                    "В тесте используются вопросы разных типов:"
                )
                normalized.append("o Выбор одного или нескольких правильных ответов.")
                normalized.append("o Заполнение пропусков в коде.")
                normalized.append("o Исправление ошибок в коде.")
                normalized.append("o Соответствие между терминами и их определениями.")
                normalized.append("Критерии оценки:")
                normalized.append("Зачет выставляется при соблюдении следующих условий:")
                normalized.append("o Все практические работы по текущему контролю выполнены и приняты преподавателем.")
                normalized.append("o Итоговое тестирование пройдено не менее чем на 70%.")
                normalized.append("Незачет ставится в случаях:")
                normalized.append("o Не выполнены все практические работы по текущему контролю.")
                normalized.append("o Итоговое тестирование выполнено менее чем на 70%.")

                index += 1
                while index < len(lines):
                    current = lines[index].strip()
                    if current.startswith("Пример тестовых вопросов:"):
                        normalized.append("Пример тестовых вопросов:")
                        break
                    index += 1
                index += 1
                continue

            if line:
                normalized.append(line)
            else:
                if normalized and normalized[-1] != "":
                    normalized.append("")
            index += 1

        while normalized and not normalized[0]:
            normalized.pop(0)
        while normalized and not normalized[-1]:
            normalized.pop()
        return "\n".join(normalized)

    def _sanitize_working_program_extra_lines(self, raw_text: str) -> list[str]:
        blacklist = (
            "конечно",
            "я готов помочь",
            "я могу помочь",
            "в этой секции",
            "вот несколько тем",
            "можно рассмотреть",
            "мы можем обсудить",
            "готов помочь вам",
        )
        lines: list[str] = []
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            normalized = re.sub(r"\s+", " ", line).strip()
            lowered = normalized.lower()
            if any(marker in lowered for marker in blacklist):
                continue
            if normalized.endswith(":") and "тема" not in lowered and "содержание" not in lowered:
                continue
            if len(normalized.split()) < 3:
                continue
            lines.append(normalized)
        return lines

    def _normalize_working_programs_block(self, text: str) -> str:
        cleaned = text.replace("РІР‚Сћ", "•").replace("вЂў", "•")
        blacklist = (
            "конечно",
            "я готов помочь",
            "я могу помочь",
            "в этой секции",
            "вот несколько тем",
            "можно рассмотреть",
            "мы можем обсудить",
            "готов помочь вам",
        )
        lines = [line.strip() for line in cleaned.splitlines()]
        normalized: list[str] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            lowered = line.lower()

            if any(marker in lowered for marker in blacklist) or "###" in line:
                index += 1
                continue

            plain_line = re.sub(r"^[•oо·\-–]\s*", "", line).strip()

            if plain_line.startswith("Форма промежуточной аттестации:"):
                normalized.append("Форма промежуточной аттестации: зачет.")
                normalized.append(
                    "Зачет проводится в форме тестирования. Слушатель проходит тест из 30 вопросов по всем темам модуля. В тесте используются вопросы разных типов:"
                )
                normalized.append("Выбор одного или нескольких правильных ответов.")
                normalized.append("Заполнение пропусков в коде.")
                normalized.append("Исправление ошибок в коде.")
                normalized.append("Соответствие между терминами и их определениями.")
                normalized.append("Критерии оценки:")
                normalized.append("Зачет выставляется при соблюдении следующих условий:")
                normalized.append("Все практические работы по текущему контролю выполнены и приняты преподавателем.")
                normalized.append("Итоговое тестирование пройдено не менее чем на 70%.")
                normalized.append("Незачет ставится в случаях:")
                normalized.append("Не выполнены все практические работы по текущему контролю.")
                normalized.append("Итоговое тестирование выполнено менее чем на 70%.")
                normalized.append("Пример тестовых вопросов:")

                while index < len(lines) and not lines[index].strip().startswith("Пример тестовых вопросов:"):
                    index += 1
                if index < len(lines):
                    index += 1

                current_question: list[str] = []
                while index < len(lines):
                    current = lines[index].strip()
                    current_plain = re.sub(r"^[•oо·\-–]\s*", "", current).strip()
                    if not current_plain:
                        if current_question:
                            normalized.append("\n".join(current_question))
                            current_question = []
                        index += 1
                        continue
                    if current_plain.startswith(("Модуль ", "Тема ", "Форма промежуточной аттестации:", "2.5.")):
                        break
                    if re.match(r"^\d+\.", current_plain):
                        if current_question:
                            normalized.append("\n".join(current_question))
                        current_question = [current_plain]
                    else:
                        if not current_question:
                            current_question = [current_plain]
                        else:
                            current_question.append(current_plain)
                    index += 1
                if current_question:
                    normalized.append("\n".join(current_question))
                continue

            if plain_line:
                normalized.append(plain_line)
            elif normalized and normalized[-1] != "":
                normalized.append("")
            index += 1

        while normalized and not normalized[0]:
            normalized.pop(0)
        while normalized and not normalized[-1]:
            normalized.pop()
        return "\n".join(normalized)

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

    def _build_assessment_block(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> AssessmentBlock:
        module_count = len(modules)
        return AssessmentBlock(
            current_control_block="\n".join(
                [
                    "Форма: Выполнение практических заданий.",
                    "Слушатель должен сдать все предусмотренные в модуле практические задания. Работы проверяются на корректность выполнения и соответствие требованиям.",
                ]
            ),
            intermediate_attestation_block="\n".join(
                [
                    "Зачет проводится в форме тестирования:",
                    "Слушатель проходит тест из 30 вопросов по всем темам модуля.",
                    "В тесте используются вопросы следующих типов:",
                    "Выбор одного или нескольких правильных ответов.",
                    "Заполнение пропусков в коде.",
                    "Исправление ошибок в коде.",
                    "Соответствие между терминами и их определениями.",
                    "Критерии оценки:",
                    "Зачет ставится при соблюдении условий:",
                    "Все практические работы по текущему контролю выполнены и приняты преподавателем.",
                    "Итоговое тестирование пройдено не менее чем на 70%.",
                    "Незачет ставится, если:",
                    "Не выполнены все практические работы по текущему контролю.",
                    "Итоговое тестирование выполнено менее чем на 70%.",
                ]
            ),
            final_attestation_intro_block="",
            final_attestation_form_and_goals_block="\n".join(
                [
                    "Форма: экзамен (защита портфолио).",
                    f"Цели: подтвердить результаты обучения демонстрацией артефактов по всем {module_count} модулям, умением их воспроизводить, обосновывать принятые решения и отвечать на вопросы комиссии.",
                ]
            ),
            portfolio_requirements_block=self._build_portfolio_requirements_block(seed, modules),
            attestation_procedure_block="\n".join(
                [
                    "Формат: видеоконференция с демонстрацией.",
                    "Продолжительность защиты: до 25 минут.",
                    "10–12 минут — доклад и демонстрация артефактов;",
                    "10–15 минут — ответы на вопросы комиссии.",
                    "Идентификация: согласно локальным регламентам организации, возможно ведение записи с согласия слушателя.",
                    "Допустимые материалы: собственный репозиторий, официальная документация инструментов.",
                ]
            ),
            report_structure_block="\n".join(
                [
                    "Краткий обзор портфолио (по модулям).",
                    "Демонстрация запуска 1–2 ключевых артефактов (локально/в контейнере/через CI).",
                    "Пояснение принятых технических решений, ограничений и направлений улучшения.",
                ]
            ),
            commission_questions_block="\n".join(self._build_commission_questions(seed, modules)),
            results_and_retake_block="\n".join(
                [
                    "Итог объявляется в день защиты/по итогам заседания комиссии, оформляется протокол.",
                    "При «неудовлетворительно» допускается пересдача в сроки, определенные локальным актом; к пересдаче — обновленное портфолио с учетом замечаний.",
                ]
            ),
            exam_grading_criteria_block=self._build_exam_grading_criteria_block(modules),
        )

    def _normalize_assessment_block(self, assessment: dict) -> AssessmentBlock:
        fallback = self._fallback_assessment()
        normalized: dict[str, str] = {}
        for key, fallback_value in fallback.items():
            value = assessment.get(key)
            normalized[key] = self._normalize_paragraph_block(value) if isinstance(value, str) and value.strip() else fallback_value
        return AssessmentBlock(**normalized)

    def _build_portfolio_requirements_block(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> str:
        folder_names = ", ".join(f"module-{module.number}/…" for module in modules)
        env_files = self._portfolio_environment_files(seed)
        lines = [
            "Срок предоставления: не позднее чем за 24 часа до защиты — ссылка на публичный репозиторий (GitHub/GitLab, бесплатные версии).",
            "Структура репозитория:",
            "README.md — оглавление, инструкция запуска, требования к окружению.",
            "PORTFOLIO.md — перечень артефактов по модулям, что проверяют и как воспроизводить.",
            f"Папки {folder_names} с кодом/скриптами/конфигурациями и, при необходимости, {env_files}.",
            "Минимальный состав по модулям:",
        ]
        lines.extend(self._portfolio_module_lines(seed, modules))
        return "\n".join(lines)

    def _portfolio_environment_files(self, seed: CourseSeedRequest) -> str:
        tags = self._course_tags(seed)
        candidates: list[str] = []
        if "python" in tags:
            candidates.append("requirements.txt")
        if "javascript" in tags or "node" in tags:
            candidates.append("package.json")
        if "docker" in tags or "devops" in tags:
            candidates.append("docker-compose.yml")
        return " / ".join(candidates) if candidates else "конфигурационных файлов проекта"

    def _portfolio_module_lines(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> list[str]:
        lines: list[str] = []
        for module in modules:
            lines.append(f"Модуль {module.number} ({module.name}): {self._portfolio_module_artifacts(seed, module)}")
        return lines

    def _portfolio_module_artifacts(self, seed: CourseSeedRequest, module: ModuleDraft) -> str:
        pairs = self._working_program_pairs(seed, module)
        theme_titles = [theme for theme, _ in pairs[:3]]
        practice_titles = [self._shorten_practice_title(practice) for _theme, practice in pairs[:2]]
        tags = self._course_tags(seed)
        haystack = f"{module.name} {module.summary}".casefold()

        if any(token in haystack for token in ("react", "frontend", "jsx", "spa")):
            return "интерфейсные компоненты, маршрутизация, работа с состоянием, интеграция с API и итоговый пользовательский сценарий."
        if any(token in haystack for token in ("node", "express", "backend", "rest api", "jwt")):
            return "серверное приложение, маршруты и middleware, REST API, аутентификация и проверка работы ключевых endpoint."
        if any(token in haystack for token in ("selenium", "playwright", "pytest", "тестир", "qa")):
            return "автотесты пользовательских и API-сценариев, фикстуры, отчеты и воспроизводимый тестовый контур."
        if any(token in haystack for token in ("docker", "kubernetes", "ci/cd", "terraform", "ansible", "devops")):
            return "конфигурации контейнеризации и развертывания, CI/CD-сценарии, эксплуатационные скрипты и подтверждение мониторинга/логирования."
        if "networks" in tags or any(token in haystack for token in ("сет", "маршрутиз", "коммутац", "vlan", "tcp/ip")):
            return "схемы и конфигурации сетевой инфраструктуры, настройки маршрутизации/коммутации, результаты диагностики и мониторинга сети."
        if theme_titles or practice_titles:
            parts = []
            if theme_titles:
                parts.append("артефакты по темам: " + "; ".join(theme_titles))
            if practice_titles:
                parts.append("подтверждающие практические результаты: " + "; ".join(practice_titles))
            return "; ".join(parts) + "."
        return "материалы и результаты практических работ по модулю, инструкции воспроизведения и подтверждающие файлы проекта."

    def _shorten_practice_title(self, practice: str) -> str:
        normalized = re.sub(r"^Практическая работа №\d+\.\s*", "", practice).strip()
        return normalized[0].lower() + normalized[1:] if normalized else practice

    def _build_commission_questions(self, seed: CourseSeedRequest, modules: list[ModuleDraft]) -> list[str]:
        questions: list[str] = []
        for module in modules:
            questions.extend(self._module_commission_questions(seed, module))
        deduplicated: list[str] = []
        for question in questions:
            if question not in deduplicated:
                deduplicated.append(question)
        return deduplicated[:9]

    def _module_commission_questions(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[str]:
        haystack = f"{module.name} {module.summary}".casefold()
        if any(token in haystack for token in ("react", "frontend", "jsx", "spa")):
            return [
                f"Каким образом организована компонентная структура и управление состоянием в модуле «{module.name}»?",
                f"Как у вас реализовано взаимодействие frontend-части с API и какие риски пользовательских сценариев вы учитывали в модуле «{module.name}»?",
            ]
        if any(token in haystack for token in ("node", "express", "backend", "rest api", "jwt")):
            return [
                f"Как устроена серверная архитектура и маршрутизация в артефактах модуля «{module.name}»?",
                f"Каким образом в модуле «{module.name}» реализованы аутентификация, валидация входных данных и обработка ошибок API?",
            ]
        if any(token in haystack for token in ("docker", "kubernetes", "ci/cd", "terraform", "ansible", "devops")):
            return [
                f"Как воспроизводится инфраструктурный артефакт модуля «{module.name}» с нуля и какие конфигурационные файлы для этого используются?",
                f"Какие этапы CI/CD, мониторинга или управления конфигурациями вы реализовали в модуле «{module.name}» и как подтверждается их работоспособность?",
            ]
        if any(token in haystack for token in ("selenium", "playwright", "pytest", "тестир", "qa")):
            return [
                f"Как вы организовали фикстуры, тестовые данные и структуру автотестов в модуле «{module.name}»?",
                f"Какие подходы к локаторам, ожиданиям и анализу нестабильности тестов вы применяли в модуле «{module.name}»?",
            ]
        if any(token in haystack for token in ("сет", "маршрутиз", "коммутац", "vlan", "tcp/ip")):
            return [
                f"Какую топологию, адресацию и сетевые протоколы вы заложили в решениях модуля «{module.name}»?",
                f"Какими средствами вы подтверждаете корректность маршрутизации, коммутации и диагностики в модуле «{module.name}»?",
            ]
        if any(token in haystack for token in ("данных", "sql", "postgres", "mongodb", "orm")):
            return [
                f"Какие модели данных, запросы или механизмы хранения реализованы в артефактах модуля «{module.name}»?",
                f"Как вы обеспечили корректность, производительность и воспроизводимость решений по модулю «{module.name}»?",
            ]
        return [
            f"Какие ключевые артефакты входят в портфолио по модулю «{module.name}» и как они воспроизводятся на защите?",
            f"Какие технические решения, ограничения и направления развития вы выделяете по результатам модуля «{module.name}»?",
        ]

    def _build_exam_grading_criteria_block(self, modules: list[ModuleDraft]) -> str:
        module_count = len(modules)
        return "\n".join(
            [
                "Оценивание ведется по 5 критериям (каждый — 0–20 баллов):",
                f"Полнота и соответствие программе: покрытие всех {module_count} модулей, наличие минимального набора артефактов, соответствие заявленным темам.",
                "Качество реализации: структура кода/проекта, читаемость, использование профильных инструментов и корректность воспроизведения решений.",
                "Автоматизация и воспроизводимость: понятные инструкции запуска, стабильность прогонов, наличие контрольных артефактов и отчетности.",
                "Аргументация и профессиональная терминология: обоснование решений, анализ рисков, предложения по улучшению, корректное употребление терминов.",
                "Коммуникация и демонстрация: логика доклада, ясность ответов, умение показать работу артефактов в режиме live-демо.",
                "Порог успешности: не менее 60 баллов из 100 и отсутствие «критического дефицита» (отсутствия артефактов целого модуля). При непрохождении — «неудовлетворительно», назначается пересдача.",
                "Перевод баллов в отметку:",
                "«отлично» (5) — 90–100.",
                "«хорошо» (4) — 75–89.",
                "«удовлетворительно» (3) — 60–74.",
                "«неудовлетворительно» (2) — <60 или отсутствие артефактов по модулю.",
            ]
        )

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

    def _working_program_pairs(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[tuple[str, str]]:
        source_pairs = self._source_working_program_pairs(seed, module)
        if source_pairs:
            return source_pairs

        exact = self._exact_working_program_pairs(seed, module)
        if exact:
            return exact

        themes = module.theme_titles[:8] or [module.name]
        return [
            (
                theme,
                f"Практическая работа №{index}. Выполнение задания по теме «{theme}».",
            )
            for index, theme in enumerate(themes, start=1)
        ]

    def _exact_theme_titles(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[str]:
        source_pairs = self._source_working_program_pairs(seed, module)
        if source_pairs:
            return [theme for theme, _practice in source_pairs]

        exact = self._exact_working_program_pairs(seed, module)
        return [theme for theme, _practice in exact]

    def _is_backend_javascript_course(self, seed: CourseSeedRequest) -> bool:
        source_url = str(seed.source_url or "").lower()
        haystack = " ".join(
            [
                seed.course_name,
                seed.professional_area,
                seed.training_goal,
                seed.brief_description,
                " ".join(module.name for module in seed.modules_seed),
                " ".join(module.summary for module in seed.modules_seed),
                source_url,
            ]
        ).lower()
        return (
            "backend" in haystack
            and "javascript" in haystack
            and ("node.js" in haystack or "nodejs" in haystack or "express" in haystack)
        ) or "backend-%d1%80%d0%b0%d0%b7%d1%80%d0%b0%d0%b1%d0%be%d1%82%d0%ba%d0%b0" in source_url

    def _source_working_program_pairs(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[tuple[str, str]]:
        outline = self._source_outline(seed)
        if not outline:
            return []
        return outline.get(self._normalize_module_key(module.name), [])

    def _source_outline(self, seed: CourseSeedRequest) -> dict[str, list[tuple[str, str]]]:
        source_url = str(seed.source_url or "").strip()
        if not source_url or "25-12.ru/courses/" not in source_url:
            return {}
        cached = self._source_outline_cache.get(source_url)
        if cached is not None:
            return cached

        try:
            response = requests.get(source_url, timeout=15)
            response.raise_for_status()
            outline = self._parse_25_12_outline(response.text)
        except Exception:
            outline = {}

        self._source_outline_cache[source_url] = outline
        return outline

    def _parse_25_12_outline(self, html_text: str) -> dict[str, list[tuple[str, str]]]:
        cleaned = re.sub(r"(?is)<script.*?>.*?</script>", "\n", html_text)
        cleaned = re.sub(r"(?is)<style.*?>.*?</style>", "\n", cleaned)
        cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"(?i)</(p|div|li|ul|ol|h1|h2|h3|h4|h5|h6|section|article)>", "\n", cleaned)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = html.unescape(cleaned)
        lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
        lines = [line for line in lines if line]

        outline: dict[str, list[tuple[str, str]]] = {}
        current_module: str | None = None
        pending_theme: str | None = None
        awaiting_title = False

        for line in lines:
            module_match = re.match(r"^Модуль\s+\d+\.\s+(.+)$", line)
            if module_match:
                current_module = self._normalize_module_key(module_match.group(1))
                outline.setdefault(current_module, [])
                pending_theme = None
                awaiting_title = False
                continue

            if current_module is None:
                continue

            if re.fullmatch(r"\d+\.\d+", line):
                awaiting_title = True
                continue

            if not awaiting_title:
                continue

            awaiting_title = False
            if line.startswith("Практическая работа"):
                if pending_theme:
                    outline[current_module].append((pending_theme, line))
                    pending_theme = None
                continue

            pending_theme = line

        return {key: value for key, value in outline.items() if value}

    def _normalize_module_key(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().lower()

    def _exact_working_program_pairs(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[tuple[str, str]]:
        if self._is_backend_javascript_course(seed):
            backend_map: dict[str, list[tuple[str, str]]] = {
                "Программирование на языке JavaScript": [
                    ("Введение в JavaScript и настройка окружения", "Практическая работа №1. Подключение JavaScript и работа в консоли"),
                    ("Переменные и типы данных в JavaScript", "Практическая работа №2. Работа с переменными и преобразование типов"),
                    ("Операторы и выражения в JavaScript", "Практическая работа №3. Использование операторов и выражений"),
                    ("Условные конструкции и ветвления", "Практическая работа №4. Написание условных конструкций"),
                    ("Циклы в JavaScript", "Практическая работа №5. Создание циклических алгоритмов"),
                    ("Функции в JavaScript", "Практическая работа №6. Написание пользовательских функций"),
                    ("Работа с областями видимости и контекстом this", "Практическая работа №7. Работа с this и контекстом функций"),
                    ("Массивы и их методы", "Практическая работа №8. Работа с массивами и методами обработки данных"),
                    ("Объекты в JavaScript", "Практическая работа №9. Создание и работа с объектами"),
                    ("Деструктуризация и spread/rest-операторы", "Практическая работа №10. Использование деструктуризации и spread/rest"),
                    ("Основы работы с DOM", "Практическая работа №11. Манипуляция DOM-элементами"),
                    ("Обработчики событий в JavaScript", "Практическая работа №12. Работа с обработчиками событий"),
                    ("Таймеры и задержки", "Практическая работа №13. Работа с таймерами и задержками"),
                    ("Промисы и работа с асинхронным кодом", "Практическая работа №14. Создание и работа с промисами"),
                    ("Async/Await и обработка данных", "Практическая работа №15. Использование fetch() и async/await"),
                    ("Модули в JavaScript (import/export)", "Практическая работа №16. Работа с модулями JavaScript"),
                    ("Работа с localStorage и sessionStorage", "Практическая работа №17. Хранение пользовательских данных"),
                    ("Работа с API и JSON", "Практическая работа №18. Запрос к API и отображение данных"),
                ],
                "Работа с базами данных и интеграция с backend": [
                    ("Введение в Node.js и настройка окружения", "Практическая работа №1. Установка Node.js и написание первого скрипта"),
                    ("Работа с модулями и пакеты в Node.js", "Практическая работа №2. Разработка небольшого проекта с несколькими модулями"),
                    ("Асинхронное программирование и работа с файлами", "Практическая работа №3. Реализация чтения и записи данных в файлы"),
                    ("Создание простого HTTP-сервера без Express", "Практическая работа №4. Создание простого HTTP-сервера и базовой маршрутизации"),
                    ("Установка и базовая настройка Express", "Практическая работа №5. Настройка нового проекта на Express"),
                    ("Маршрутизация и параметры запросов", "Практическая работа №6. Создание CRUD-маршрутов на Express"),
                    ("Работа с шаблонизаторами (EJS, Pug)", "Практическая работа №7. Рендер динамической страницы через шаблонизатор"),
                    ("Обработка статических файлов и логирование", "Практическая работа №8. Настройка статических файлов и логирования запросов"),
                    ("Понятие middleware и создание собственного middleware", "Практическая работа №9. Разработка логирующего middleware"),
                    ("Аутентификация и авторизация (JWT)", "Практическая работа №10. Реализация аутентификации с помощью JWT"),
                    ("Обработка ошибок и валидация данных", "Практическая работа №11. Настройка валидации данных и обработки ошибок"),
                    ("Защита API и CORS", "Практическая работа №12. Настройка и проверка CORS в Express"),
                    ("Подключение к базам данных (MongoDB, PostgreSQL)", "Практическая работа №13. Создание и чтение данных из MongoDB / PostgreSQL"),
                    ("Создание REST API и взаимодействие с клиентом", "Практическая работа №14. Реализация полнофункционального REST API"),
                    ("Асинхронность и производительность", "Практическая работа №15. Оптимизация асинхронных запросов и кеширование"),
                    ("Взаимодействие с внешними API", "Практическая работа №16. Получение и обработка данных из внешнего API"),
                    ("Логирование и мониторинг", "Практическая работа №17. Настройка логирования и базового мониторинга"),
                    ("Тестирование приложений на Node.js", "Практическая работа №18. Написание тестов для маршрутов Express"),
                ],
                "Аутентификация, авторизация и продвинутый backend": [
                    ("Введение в базы данных", "Практическая работа №1. Разработка первого приложения с базой данных"),
                    ("Основы SQL и реляционные базы данных (PostgreSQL)", "Практическая работа №2. Написание SQL-запросов для работы с данными"),
                    ("PostgreSQL: создание таблиц и связей", "Практическая работа №3. Создание таблиц и установление связей в PostgreSQL"),
                    ("Основы работы с MongoDB (NoSQL)", "Практическая работа №4. Создание коллекций и работа с документами в MongoDB"),
                    ("Работа с индексацией и оптимизация запросов в PostgreSQL", "Практическая работа №5. Создание индексов и оптимизация SQL-запросов"),
                    ("Работа с подзапросами и объединениями в PostgreSQL", "Практическая работа №6. Работа с подзапросами и объединениями в PostgreSQL"),
                    ("Хранение и обработка больших объемов данных в PostgreSQL", "Практическая работа №7. Работа с большими объемами данных в PostgreSQL"),
                    ("Операции с MongoDB: фильтрация, обновление и удаление данных", "Практическая работа №8. Фильтрация, обновление и удаление данных в MongoDB"),
                    ("Интеграция PostgreSQL с Node.js", "Практическая работа №9. Интеграция PostgreSQL с Node.js"),
                    ("Использование MongoDB с Node.js", "Практическая работа №10. Подключение и работа с MongoDB в Node.js"),
                    ("Реализация CRUD операций с PostgreSQL", "Практическая работа №11. Реализация CRUD операций с PostgreSQL"),
                    ("Реализация CRUD операций с MongoDB", "Практическая работа №12. Реализация CRUD операций с MongoDB"),
                    ("Оптимизация работы с базами данных", "Практическая работа №13. Оптимизация запросов в PostgreSQL и MongoDB"),
                    ("Безопасность в PostgreSQL", "Практическая работа №14. Реализация безопасности в PostgreSQL"),
                    ("Безопасность и защита данных в MongoDB", "Практическая работа №15. Реализация безопасности и защиты данных в MongoDB"),
                    ("Резервное копирование и восстановление данных", "Практическая работа №16. Настройка резервного копирования и восстановления"),
                    ("Моделирование данных и проектирование баз данных", "Практическая работа №17. Проектирование структуры базы данных для проекта"),
                    ("Построение многозвенных приложений с интеграцией баз данных", "Практическая работа №18. Разработка многозвенного приложения с БД"),
                ],
                "Разработка REST API и интеграция с клиентской частью WEB-приложений": [
                    ("Введение в REST API", "Практическая работа №1. Создание простого API с использованием HTTP методов"),
                    ("Разработка первого REST API", "Практическая работа №2. Разработка базового REST API на Express"),
                    ("Управление данными через API", "Практическая работа №3. Реализация операций управления данными через API"),
                    ("Работа с параметрами запроса", "Практическая работа №4. Работа с параметрами запроса в API"),
                    ("Основы авторизации и аутентификации", "Практическая работа №5. Реализация базовой аутентификации"),
                    ("Защита API с помощью JWT", "Практическая работа №6. Настройка защиты API с использованием JWT"),
                    ("Работа с CORS (Cross-Origin Resource Sharing)", "Практическая работа №7. Настройка CORS для API"),
                    ("Ограничение доступа и использование ролей", "Практическая работа №8. Реализация ролевой модели доступа"),
                    ("Введение в взаимодействие API и фронтенда", "Практическая работа №9. Интеграция API с фронтендом"),
                    ("Обработка ответов API на фронтенде", "Практическая работа №10. Обработка ответов API на клиентской стороне"),
                    ("Интеграция с фронтендом с использованием AJAX", "Практическая работа №11. Интеграция API через AJAX"),
                    ("Работа с формами на фронтенде через API", "Практическая работа №12. Отправка и обработка форм через API"),
                    ("Логирование и мониторинг API", "Практическая работа №13. Настройка логирования API"),
                    ("Обработка ошибок в REST API", "Практическая работа №14. Реализация обработки ошибок в API"),
                    ("Кэширование данных в API", "Практическая работа №15. Реализация кэширования данных"),
                    ("Разработка версионированных API", "Практическая работа №16. Создание версионированного API"),
                    ("Основы тестирования API", "Практическая работа №17. Написание тестов для REST API"),
                    ("Создание документации для API", "Практическая работа №18. Создание документации REST API"),
                ],
            }
            return backend_map.get(module.name, [])

        if (seed.constraints.standard_profile_id or "").strip().lower() != "fgos_spo_09_02_11":
            return []

        exact_map: dict[str, list[tuple[str, str]]] = {
            "Программирование на языке Python": [
                ("Введение в Python и установка среды разработки", "Практическая работа №1. Установка Python и запуск первой программы"),
                ("Переменные и типы данных", "Практическая работа №2. Работа с переменными и типами данных"),
                ("Операторы в Python", "Практическая работа №3. Вычисления и логические операции в Python"),
                ("Условные конструкции", "Практическая работа №4. Программы с условными операторами"),
                ("Циклы в Python", "Практическая работа №5. Написание циклических программ"),
                ("Работа со строками", "Практическая работа №6. Обработка строк"),
                ("Списки и кортежи", "Практическая работа №7. Работа со списками"),
                ("Словари и множества", "Практическая работа №8. Использование словарей"),
                ("Генераторы списков, тернарный оператор", "Практическая работа №9. Оптимизация кода с генераторами и lambda-функциями."),
                ("Итоговые задания по структурам данных", "Практическая работа №10. Задачи на работу со структурами данных."),
                ("Функции в Python: основы", "Практическая работа №11. Создание пользовательских функций."),
                ("Передача аргументов, *args, **kwargs", "Практическая работа №12. Работа с *args и **kwargs в пользовательских функциях."),
                ("Рекурсия в Python", "Практическая работа №13. Реализация рекурсивных алгоритмов."),
                ("Генераторы и итераторы", "Практическая работа №14. Написание собственных генераторов данных."),
                ("Работа с файлами: чтение и запись", "Практическая работа №15. Работа с файлами: чтение и запись данных."),
                ("Работа с CSV и JSON файлами", "Практическая работа №16. Чтение и запись данных в CSV и JSON."),
                ("Обработка ошибок и исключения", "Практическая работа №17. Обработка ошибок в пользовательских программах."),
                ("Работа с регулярными выражениями (re)", "Практическая работа №18. Поиск и замена данных с использованием регулярных выражений."),
            ],
            "Python для DevOps": [
                ("Введение в DevOps и автоматизацию инфраструктуры", "Практическая работа №1. Установка и настройка окружения DevOps на локальной машине."),
                ("Работа с серверами через SSH и Python", "Практическая работа №2. Написание Python-скрипта для удалённого администрирования сервера."),
                ("Основы работы с Linux и автоматизация задач", "Практическая работа №3. Автоматизация резервного копирования файлов с помощью Python."),
                ("Управление пользователями и правами доступа", "Практическая работа №4. Автоматизация управления пользователями на сервере."),
                ("Введение в Ansible: автоматизация серверных конфигураций", "Практическая работа №5. Написание Ansible-плейбука для настройки сервера."),
                ("Использование Python в Ansible", "Практическая работа №6. Создание пользовательского Ansible-модуля на Python"),
                ("Автоматизация инфраструктуры с Terraform", "Практическая работа №7. Написание Terraform-скрипта для развертывания серверов в облаке"),
                ("Python-скрипты для управления облачной инфраструктурой", "Практическая работа №8. Написание Python-скрипта для управления ресурсами"),
                ("Основы мониторинга и логирования в DevOps", "Практическая работа №9. Настройка базового мониторинга сервера с Prometheus"),
                ("Сбор метрик с помощью Prometheus", "Практическая работа №10. Разработка Python-метрик для Prometheus"),
                ("Визуализация метрик в Grafana", "Практическая работа №11. Настройка дашборда в Grafana для мониторинга серверов"),
                ("Логирование и анализ данных с ELK Stack", "Практическая работа №12. Настройка централизованного логирования с ELK Stack"),
                ("Автоматизация работы с логами", "Практическая работа №13. Создание системы логирования для DevOps инфраструктуры"),
                ("Настройка алертинга в DevOps", "Практическая работа №14. Автоматическая отправка уведомлений при сбоях системы"),
                ("Сбор и анализ системных логов", "Практическая работа №15. Автоматизированный анализ логов и мониторинг событий"),
                ("Основы CI/CD и автоматизированного развертывания", "Практическая работа №16. Настройка базового CI/CD пайплайна"),
                ("Интеграция Python-скриптов в CI/CD", "Практическая работа №17. Автоматизация тестов и деплоя с помощью CI/CD"),
                ("Kubernetes и оркестрация контейнеров", "Практическая работа №18. Деплой Python-приложения в Kubernetes"),
            ],
            "Работа с Docker и Kubernetes": [
                ("Введение в контейнеризацию и Docker", "Практическая работа №1. Установка Docker и запуск первого контейнера"),
                ("Управление образами и контейнерами", "Практическая работа №2. Управление контейнерами и образами в Docker"),
                ("Работа с реестрами Docker", "Практическая работа №3. Размещение собственного Docker-образа в Docker Hub"),
                ("Сетевое взаимодействие контейнеров", "Практическая работа №4. Создание сети и подключение нескольких контейнеров"),
                ("Основы создания образов с Dockerfile", "Практическая работа №5. Написание Dockerfile для Python-приложения"),
                ("Переменные окружения и конфигурация контейнеров", "Практическая работа №6. Использование переменных окружения в контейнерах"),
                ("Оптимизация Docker-образов", "Практическая работа №7. Оптимизация Dockerfile для уменьшения размера образа"),
                ("Работа с Docker Logs и отладка контейнеров", "Практическая работа №8. Логирование и отладка контейнеров в Docker"),
                ("Введение в Docker Compose", "Практическая работа №9. Запуск нескольких контейнеров с Docker Compose"),
                ("Связь контейнеров в Docker Compose", "Практическая работа №10. Создание связанного стека контейнеров (API + БД)"),
                ("Масштабирование контейнеров в Docker Compose", "Практическая работа №11. Масштабирование веб-приложения с Docker Compose."),
                ("Автоматизация развертывания с Docker Compose", "Практическая работа №12. Развертывание приложения в облаке с Docker Compose."),
                ("Основные концепции Kubernetes", "Практическая работа №13. Установка Minikube и запуск первого Pod"),
                ("Управление подами в Kubernetes", "Практическая работа №14. Развертывание контейнера в Pod"),
                ("Деплойменты и обновления в Kubernetes", "Практическая работа №15. Обновление приложения в Kubernetes"),
                ("Конфигурации и секреты в Kubernetes", "Практическая работа №16. Подключение ConfigMap и Secret в Pod"),
                ("Балансировка нагрузки в Kubernetes", "Практическая работа №17. Настройка балансировки нагрузки в Kubernetes"),
                ("Масштабирование приложений в Kubernetes", "Практическая работа №18. Настройка автоскейлинга в Kubernetes"),
            ],
            "Автоматизация DevOps-процессов на Python": [
                ("Основы DevOps и роль Python в автоматизации", "Практическая работа №1. Написание первого DevOps-скрипта на Python"),
                ("Работа с процессами и файлами в Python", "Практическая работа №2. Создание Python-скрипта для автоматизации работы с файлами и процессами"),
                ("Инфраструктура как код с Python", "Практическая работа №3. Написание скрипта для автоматизированного развертывания серверов с Ansible"),
                ("Управление облачными сервисами через Python", "Практическая работа №4. Написание Python-скрипта для управления облачными ресурсами"),
                ("Мониторинг DevOps-инфраструктуры с Python", "Практическая работа №5. Настройка мониторинга Python-скрипта с Prometheus"),
                ("Логирование и обработка логов", "Практическая работа №6. Настройка логирования в Python-скрипте с отправкой в централизованное хранилище"),
                ("CI/CD и автоматизация деплоя", "Практическая работа №7. Автоматизация CI/CD пайплайна с Python"),
                ("Интеграция Python с CI/CD инструментами", "Практическая работа №8. Интеграция Python-скрипта с CI/CD пайплайном"),
                ("Контейнеризация Python-приложений с Docker", "Практическая работа №9. Написание Dockerfile и создание контейнера для Python-приложения"),
                ("Автоматизация Kubernetes через Python", "Практическая работа №10. Написание Python-скрипта для работы с Kubernetes"),
                ("Масштабирование DevOps-скриптов", "Практическая работа №11. Написание асинхронного DevOps-скрипта"),
                ("Управление конфигурациями серверов через Python", "Практическая работа №12. Написание Python-скрипта для автоматизированного администрирования серверов"),
                ("Автоматизация тестирования DevOps-инфраструктуры", "Практическая работа №13. Написание тестов для DevOps-инфраструктуры"),
                ("Автоматизированный анализ уязвимостей", "Практическая работа №14. Разработка Python-скрипта для автоматического анализа безопасности"),
                ("Автоматизированный бэкап и восстановление данных", "Практическая работа №15. Написание скрипта для автоматического бэкапа и восстановления"),
                ("Автоматизация управления доступом и пользователями", "Практическая работа №16. Разработка Python-скрипта для управления учетными записями пользователей"),
                ("Настройка алертинга и уведомлений в DevOps", "Практическая работа №17. Разработка Python-скрипта для отправки уведомлений о сбоях"),
                ("Автоматизация управления сетевой инфраструктурой", "Практическая работа №18. Написание Python-скрипта для настройки сетевых устройств"),
            ],
        }
        return exact_map.get(module.name, [])

    def _exact_module_exam_questions(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[str]:
        if (seed.constraints.standard_profile_id or "").strip().lower() != "fgos_spo_09_02_11":
            return []

        exact_map: dict[str, list[str]] = {
            "Программирование на языке Python": [
                "1. Что выведет выражение `type({1, 2, 3})`?",
                "A) `<class 'list'>`  B) `<class 'set'>`  C) `<class 'dict'>`  D) `<class 'tuple'>`",
                "Ответ: B",
                "2. Как корректно открыть файл для чтения с гарантированным закрытием?",
                "A) `f = open('in.txt', 'r'); data = f.read()`",
                "B) `with open('in.txt', 'r', encoding='utf-8') as f: data = f.read()`",
                "C) `read('in.txt')`",
                "D) `open('in.txt').close()`",
                "Ответ: B",
            ],
            "Python для DevOps": [
                "1. Для чего чаще всего используют SSH в DevOps-практиках?",
                "A) Для верстки интерфейсов  B) Для удалённого администрирования серверов  C) Для сжатия архивов  D) Для компиляции Python",
                "Ответ: B",
                "2. Какой инструмент применяется для управления конфигурациями и автоматизации серверных операций?",
                "A) Figma  B) Ansible  C) SQLite  D) pandas",
                "Ответ: B",
            ],
            "Работа с Docker и Kubernetes": [
                "1. Для чего используется `Dockerfile`?",
                "A) Для описания процесса сборки образа  B) Для хранения логов контейнера  C) Для балансировки нагрузки  D) Для мониторинга метрик",
                "Ответ: A",
                "2. Какая сущность Kubernetes отвечает за управление набором Pod и обновлениями приложения?",
                "A) Secret  B) ConfigMap  C) Deployment  D) Volume",
                "Ответ: C",
            ],
            "Автоматизация DevOps-процессов на Python": [
                "1. Для чего Python часто используют в автоматизации DevOps-процессов?",
                "A) Только для фронтенда  B) Для написания скриптов сопровождения, мониторинга и деплоя  C) Только для верстки  D) Для замены Docker",
                "Ответ: B",
                "2. Что обычно включает CI/CD-пайплайн?",
                "A) Только ручной запуск сервера  B) Сборку, тестирование и доставку изменений  C) Только редактирование документации  D) Исключительно мониторинг сети",
                "Ответ: B",
            ],
        }
        return exact_map.get(module.name, [])

    def _catalog_module_exam_questions(self, seed: CourseSeedRequest, module: ModuleDraft) -> list[str]:
        haystack = f"{module.name} {module.summary}".casefold()

        def pack(question_1: tuple[str, str, str], question_2: tuple[str, str, str]) -> list[str]:
            return [
                question_1[0],
                question_1[1],
                question_1[2],
                question_2[0],
                question_2[1],
                question_2[2],
            ]

        if any(token in haystack for token in ("python", "args", "kwargs", "файл", "исключен", "регуляр")):
            return pack(
                (
                    "1. Какой менеджер контекста в Python используют для безопасной работы с файлами?",
                    "A) `match`  B) `with`  C) `lambda`  D) `yield`",
                    "Ответ: B",
                ),
                (
                    "2. Какой модуль Python применяют для работы с регулярными выражениями?",
                    "A) `json`  B) `math`  C) `re`  D) `pathlib`",
                    "Ответ: C",
                ),
            )

        if any(token in haystack for token in ("react", "jsx", "frontend", "spa", "компонент")):
            return pack(
                (
                    "1. Что в React отвечает за хранение изменяемого состояния компонента?",
                    "A) props  B) state  C) route  D) key",
                    "Ответ: B",
                ),
                (
                    "2. Для чего используют JSX в React-приложении?",
                    "A) Для описания интерфейса в синтаксисе, близком к HTML  B) Для настройки базы данных  C) Для сборки Docker-образов  D) Для маршрутизации запросов Nginx",
                    "Ответ: A",
                ),
            )

        if any(token in haystack for token in ("node", "express", "backend", "rest api", "jwt", "cors")):
            return pack(
                (
                    "1. Какой фреймворк чаще всего используют вместе с Node.js для создания REST API в учебных проектах?",
                    "A) Django  B) Express  C) Flask  D) Spring",
                    "Ответ: B",
                ),
                (
                    "2. Для чего middleware применяется в Express-приложении?",
                    "A) Для обработки запроса и ответа между этапами маршрута  B) Для верстки интерфейса  C) Для компиляции TypeScript без Node.js  D) Для хранения данных вместо базы данных",
                    "Ответ: A",
                ),
            )

        if any(token in haystack for token in ("selenium", "playwright", "pytest", "тестир", "qa", "allure")):
            return pack(
                (
                    "1. Для чего в pytest используют фикстуры?",
                    "A) Для подготовки и переиспользования окружения тестов  B) Для верстки страниц  C) Для построения ER-диаграмм  D) Для настройки VLAN",
                    "Ответ: A",
                ),
                (
                    "2. Что помогает снизить нестабильность UI-автотестов в Selenium и Playwright?",
                    "A) Случайные паузы  B) Явные ожидания и корректные локаторы  C) Отключение проверок  D) Удаление assert-ов",
                    "Ответ: B",
                ),
            )

        if any(token in haystack for token in ("docker", "kubernetes", "container", "оркестр", "ci/cd", "terraform", "ansible", "devops")):
            return pack(
                (
                    "1. Для чего в Docker используют Dockerfile?",
                    "A) Для описания шагов сборки образа  B) Для хранения логов контейнера  C) Для настройки SQL-запросов  D) Для описания Git-веток",
                    "Ответ: A",
                ),
                (
                    "2. Что обычно входит в CI/CD-пайплайн?",
                    "A) Только ручная публикация  B) Сборка, тестирование и доставка изменений  C) Только чтение документации  D) Только запуск базы данных",
                    "Ответ: B",
                ),
            )

        if any(token in haystack for token in ("postgres", "mongodb", "sql", "баз", "orm", "данных")):
            return pack(
                (
                    "1. Какой оператор SQL используют для выборки данных из таблицы?",
                    "A) INSERT  B) SELECT  C) DELETE  D) UPDATE",
                    "Ответ: B",
                ),
                (
                    "2. Для чего ORM применяют в прикладной разработке?",
                    "A) Для объектного доступа к данным без ручной сборки каждого SQL-запроса  B) Для настройки видеосвязи  C) Для рисования интерфейсов  D) Для маршрутизации пакетов",
                    "Ответ: A",
                ),
            )

        if any(token in haystack for token in ("сет", "маршрутиз", "коммутац", "tcp/ip", "vlan", "dhcp", "dns")):
            return pack(
                (
                    "1. Какая модель описывает взаимодействие сетевых уровней при передаче данных?",
                    "A) CRUD  B) OSI  C) SOLID  D) ACID",
                    "Ответ: B",
                ),
                (
                    "2. Для чего в сети используют VLAN?",
                    "A) Для логического разделения трафика  B) Для хранения исходного кода  C) Для запуска контейнеров  D) Для сборки Python-пакетов",
                    "Ответ: A",
                ),
            )

        return pack(
            (
                f"1. Что является ключевым практическим результатом модуля «{module.name}»?",
                "A) Только просмотр материалов  B) Корректно выполненное и проверенное практическое задание  C) Только участие в вебинаре  D) Только чтение конспекта",
                "Ответ: B",
            ),
            (
                f"2. Что подтверждает успешное освоение содержания модуля «{module.name}»?",
                "A) Наличие заметок  B) Выполнение практической работы и понимание используемых инструментов  C) Только регистрация в системе  D) Просмотр одного занятия",
                "Ответ: B",
            ),
        )

    def _topic_content_points(self, module: ModuleDraft, theme: str) -> list[str]:
        topic = theme.lower()

        keyword_map: list[tuple[tuple[str, ...], list[str]]] = [
            (
                ("установка среды", "установка python"),
                [
                    "история и особенности Python;",
                    "установка Python и настройка интерпретатора;",
                    "выбор среды разработки: PyCharm, VS Code, Jupyter Notebook;",
                    "запуск первой программы и проверка рабочего окружения.",
                ],
            ),
            (
                ("переменные", "типы данных"),
                [
                    "динамическая типизация в Python;",
                    "числовые, строковые и логические типы данных;",
                    "операции ввода и вывода данных;",
                    "приведение типов и типовые ошибки при работе с данными.",
                ],
            ),
            (
                ("операторы",),
                [
                    "арифметические, логические и сравнительные операторы;",
                    "приоритет и порядок выполнения операций;",
                    "составные выражения и вычисления в Python;",
                    "типовые ошибки в логических выражениях.",
                ],
            ),
            (
                ("условные конструкции",),
                [
                    "операторы if, elif, else;",
                    "логические ветвления и проверка условий;",
                    "вложенные условные конструкции;",
                    "проектирование сценариев выбора по условиям.",
                ],
            ),
            (
                ("циклы",),
                [
                    "циклы for и while;",
                    "управление итерациями: break, continue, else;",
                    "вложенные циклы и обработка последовательностей;",
                    "типовые шаблоны циклических алгоритмов.",
                ],
            ),
            (
                ("строк",),
                [
                    "базовые операции со строками;",
                    "методы обработки и преобразования строковых данных;",
                    "поиск, замена и форматирование строк;",
                    "решение прикладных задач обработки текста.",
                ],
            ),
            (
                ("списки", "кортеж"),
                [
                    "создание и изменение списков и кортежей;",
                    "индексация, срезы и перебор элементов;",
                    "базовые методы работы с коллекциями;",
                    "подготовка данных в структурированных списках.",
                ],
            ),
            (
                ("словар", "множест"),
                [
                    "ключи, значения и операции со словарями;",
                    "использование множеств для фильтрации и сравнения данных;",
                    "перебор элементов и словарные методы;",
                    "решение задач хранения и поиска данных.",
                ],
            ),
            (
                ("регулярн", "re"),
                [
                    "синтаксис регулярных выражений;",
                    "поиск, извлечение и замена шаблонов в тексте;",
                    "использование модуля re в Python;",
                    "разбор типовых шаблонов обработки строковых данных.",
                ],
            ),
            (
                ("ssh",),
                [
                    "основы протокола SSH и безопасного удалённого доступа;",
                    "подключение к серверу и выполнение команд из Python;",
                    "использование библиотек для удалённого администрирования;",
                    "обработка ошибок соединения и журналирование операций.",
                ],
            ),
            (
                ("linux",),
                [
                    "командная строка Linux и базовые системные утилиты;",
                    "автоматизация типовых административных задач;",
                    "работа с файлами, каталогами и правами доступа;",
                    "подготовка скриптов сопровождения инфраструктуры.",
                ],
            ),
            (
                ("ansible",),
                [
                    "архитектура Ansible и принципы работы плейбуков;",
                    "инвентори, модули и переменные Ansible;",
                    "автоматизация настройки серверных конфигураций;",
                    "интеграция Python-логики в сценарии Ansible.",
                ],
            ),
            (
                ("terraform",),
                [
                    "принципы инфраструктуры как кода;",
                    "описание ресурсов и зависимостей в Terraform;",
                    "планирование и применение инфраструктурных изменений;",
                    "автоматизация развёртывания облачных ресурсов.",
                ],
            ),
            (
                ("prometheus",),
                [
                    "метрики, exporters и модель сбора данных Prometheus;",
                    "настройка мониторинга и правил сбора метрик;",
                    "экспорт пользовательских метрик из Python;",
                    "анализ состояния сервисов по метрикам и алертам.",
                ],
            ),
            (
                ("grafana",),
                [
                    "подключение источников данных в Grafana;",
                    "создание дашбордов и визуализация метрик;",
                    "панели, переменные и алерты Grafana;",
                    "подготовка мониторинговых представлений для эксплуатации.",
                ],
            ),
            (
                ("elk", "логирование", "логи"),
                [
                    "принципы централизованного логирования;",
                    "сбор, маршрутизация и хранение логов;",
                    "анализ событий и журналов средствами ELK Stack;",
                    "автоматизация обработки логов и поиск инцидентов.",
                ],
            ),
            (
                ("ci/cd", "деплоя", "развертывания", "развертывания"),
                [
                    "этапы конвейера непрерывной интеграции и поставки;",
                    "автоматизация сборки, тестирования и деплоя;",
                    "интеграция Python-скриптов в пайплайны;",
                    "контроль качества и отслеживание результатов поставки.",
                ],
            ),
            (
                ("docker", "контейнер"),
                [
                    "архитектура Docker и основы контейнеризации;",
                    "образы, контейнеры, Dockerfile и Docker Compose;",
                    "сети, тома, переменные окружения и журналы контейнеров;",
                    "сборка и эксплуатация контейнеризированных приложений.",
                ],
            ),
            (
                ("kubernetes", "pod", "деплоймент", "configmap", "secret", "автоскейлинг"),
                [
                    "базовые сущности Kubernetes и организация кластера;",
                    "управление Pod, Deployment и Service;",
                    "работа с конфигурациями, секретами и балансировкой нагрузки;",
                    "масштабирование и сопровождение приложений в Kubernetes.",
                ],
            ),
            (
                ("процессами", "файлами"),
                [
                    "управление процессами и файловыми операциями средствами Python;",
                    "модули subprocess, pathlib, os и shutil;",
                    "автоматизация обработки файлов и системных задач;",
                    "подготовка служебных скриптов администрирования.",
                ],
            ),
            (
                ("уязвимост", "безопасности"),
                [
                    "основы поиска уязвимостей в инфраструктуре и коде;",
                    "автоматизация проверок безопасности;",
                    "анализ результатов сканирования и подготовка отчётов;",
                    "снижение эксплуатационных рисков и контроль исправлений.",
                ],
            ),
            (
                ("бэкап", "восстановление"),
                [
                    "стратегии резервного копирования и восстановления данных;",
                    "автоматизация сценариев backup/restore на Python;",
                    "планирование расписаний и проверка целостности копий;",
                    "документирование процедур восстановления.",
                ],
            ),
        ]

        for keywords, bullets in keyword_map:
            if any(keyword in topic for keyword in keywords):
                return bullets

        return [
            f"ключевые понятия и подходы по теме «{theme}»;",
            f"основные инструменты и команды, используемые в теме «{theme}»;",
            f"практические сценарии применения по теме «{theme}»;",
            f"разбор типовых ошибок и ограничений по теме «{theme}».",
        ]

    def _exact_facilities(self, seed: CourseSeedRequest) -> list[FacilityEntry]:
        if (seed.constraints.standard_profile_id or "").strip().lower() != "fgos_spo_09_02_11":
            return []
        return self._catalog_facilities(seed)

    def _exact_digital_resources(self, seed: CourseSeedRequest) -> list[DigitalResourceEntry]:
        if (seed.constraints.standard_profile_id or "").strip().lower() != "fgos_spo_09_02_11":
            return []
        return self._catalog_digital_resources(seed)

    def _topic_self_study_points(self, module: ModuleDraft, theme: str) -> list[str]:
        return [
            f"внеаудиторная работа: изучение документации и примеров по теме «{theme}» — 1 ч.;",
            f"подготовка и разбор самостоятельного мини-задания по теме «{theme}» — 1 ч.",
        ]

    def _compose_theme_titles(self, module_name: str, module_summary: str, raw_themes) -> list[str]:
        suggested = [str(value).strip() for value in raw_themes if str(value).strip()]
        catalog = self._catalog_theme_titles(module_name, module_summary)

        combined: list[str] = []
        for theme in [*suggested, *catalog]:
            normalized = re.sub(r"\s+", " ", theme).strip()
            if normalized and normalized not in combined:
                combined.append(normalized)

        if len(combined) < 8:
            combined.extend(
                item
                for item in [
                    f"Введение в модуль «{module_name}»",
                    f"Ключевые инструменты и подходы по теме «{module_name}»",
                    f"Практика применения решений по теме «{module_name}»",
                    f"Контроль качества и документирование по теме «{module_name}»",
                    f"Итоговое практическое задание по модулю «{module_name}»",
                ]
                if item not in combined
            )

        return combined[:16]

    def _catalog_theme_titles(self, module_name: str, module_summary: str) -> list[str]:
        haystack = f"{module_name} {module_summary}".lower()

        if "docker" in haystack or "kubernetes" in haystack:
            return [
                "Контейнеризация приложений и роль Docker в DevOps",
                "Установка Docker и обзор архитектуры контейнеров",
                "Образы, контейнеры и команды жизненного цикла",
                "Dockerfile: инструкции, слои и оптимизация сборки",
                "Работа с реестрами образов и публикация контейнеров",
                "Docker Compose и запуск многоконтейнерных приложений",
                "Сети, тома и хранение данных в Docker",
                "Введение в Kubernetes и базовые сущности кластера",
                "Pods, Deployments, Services и ConfigMaps",
                "Secrets, ingress и управление доступом",
                "Масштабирование, обновление и откат приложений",
                "Minikube и локальная отработка сценариев оркестрации",
                "Диагностика контейнеров и журналирование",
                "Практическая сборка и деплой контейнеризированного сервиса",
            ]

        if "devops" in haystack and ("автоматизац" in haystack or "ci/cd" in haystack or "мониторинг" in haystack):
            return [
                "Подходы к автоматизации DevOps-процессов на Python",
                "Автоматизация проверки состояния сервисов и окружений",
                "Сбор, анализ и фильтрация логов средствами Python",
                "Работа с метриками, алертами и интеграцией мониторинга",
                "Автоматизация задач CI/CD и обработки артефактов",
                "Скрипты для управления Kubernetes и облачными ресурсами",
                "Интеграция Python-утилит с API внешних сервисов",
                "Обработка конфигураций, шаблонов и секретов",
                "Проверка инфраструктурного кода и тестирование сценариев",
                "Автоматизация резервного копирования и восстановления",
                "Поиск уязвимостей и анализ зависимостей",
                "Документирование эксплуатационных сценариев",
                "Практика построения автоматизированного пайплайна",
            ]

        if "devops" in haystack or "terraform" in haystack or "ansible" in haystack or "ssh" in haystack:
            return [
                "Роль Python в DevOps и автоматизации инфраструктуры",
                "Работа с Linux и командной строкой для автоматизации",
                "SSH-подключения и выполнение удалённых команд из Python",
                "Автоматизация администрирования пользователей и файлов",
                "Обработка конфигурационных файлов и переменных окружения",
                "Основы Ansible и автоматизация типовых операций",
                "Terraform и инфраструктура как код",
                "Автоматизация развёртывания сервисов и окружений",
                "CI/CD: стадии, артефакты и проверка сборок",
                "Мониторинг, журналирование и диагностика инцидентов",
                "Интеграция Python-скриптов с REST API и webhook",
                "Практика построения DevOps-сценариев сопровождения",
            ]

        if "python" in haystack:
            return [
                "Введение в Python и установка среды разработки",
                "Переменные, типы данных и операции",
                "Условные конструкции и логические выражения",
                "Циклы и управление выполнением программы",
                "Строки, списки, кортежи и словари",
                "Множества и базовые операции с коллекциями",
                "Функции, параметры и область видимости",
                "Обработка исключений и отладка программ",
                "Работа с файлами и файловой системой",
                "Модули, пакеты и импорт зависимостей",
                "Основы объектно-ориентированного программирования",
                "Регулярные выражения и обработка текстовых данных",
                "Работа с JSON, CSV и структурированными данными",
                "Итоговая практическая работа на Python",
            ]

        return [
            f"Введение в модуль «{module_name}»",
            f"Базовые понятия и терминология по теме «{module_name}»",
            f"Инструменты и рабочая среда по теме «{module_name}»",
            f"Практические сценарии применения по теме «{module_name}»",
            f"Контроль качества и проверка результатов по теме «{module_name}»",
            f"Документирование решений по теме «{module_name}»",
        ]

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
