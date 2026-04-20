from __future__ import annotations

from dataclasses import dataclass
import re
import json
from pathlib import Path

from app.schemas.course import CourseSeedRequest
from app.schemas.draft import ActivityMatrixEntry, LaborFunctionEntry
from app.services.errors import DraftValidationError

DEFAULT_STANDARD_PROFILE_ID = "fgos_spo_09_02_07"
LEGACY_PROFILE_IDS = {
    "web_developer_dpo": DEFAULT_STANDARD_PROFILE_ID,
}
_dynamic_registry_path = Path(__file__).resolve().parents[1] / "storage" / "standards" / "profiles.json"


@dataclass(frozen=True)
class StandardTrack:
    track_id: str
    qualification_title: str
    professional_objects: tuple[str, ...]
    activity_types: tuple[str, ...]
    labor_functions: tuple[LaborFunctionEntry, ...]
    activity_matrix: tuple[ActivityMatrixEntry, ...]


@dataclass(frozen=True)
class StandardProfile:
    profile_id: str
    fgos_code: str
    title: str
    order_title: str
    source_url: str
    professional_area: str
    qualification_level: str
    parallel_education_note: str
    audience_requirements: tuple[str, ...]
    additional_requirements: tuple[str, ...]
    entry_requirements: str
    tracks: tuple[StandardTrack, ...]

    def compose_standards_basis(self, track: StandardTrack, additional_standards: list[str]) -> str:
        extra = [item.strip() for item in additional_standards if item.strip()]
        base = (
            f"Программа разработана с учётом требований {self.title} "
            f"({self.order_title}), область профессиональной деятельности — {self.professional_area}. "
            f"Квалификационный акцент программы соотнесён с направлением «{track.qualification_title}»."
        )
        if not extra:
            return base
        return f"{base} Дополнительно учтены локальные материалы: {'; '.join(extra)}."

    def get_track(self, track_id: str | None) -> StandardTrack:
        resolved_track_id = (track_id or "").strip().lower()
        if not resolved_track_id:
            raise DraftValidationError("Не задан тематический трек стандарта.")
        for track in self.tracks:
            if track.track_id == resolved_track_id:
                return track
        supported = ", ".join(track.track_id for track in self.tracks)
        raise DraftValidationError(
            f"Неподдерживаемый тематический трек стандарта: {resolved_track_id}. Доступные треки: {supported}."
        )


@dataclass(frozen=True)
class ResolvedStandardProfile:
    profile: StandardProfile
    track: StandardTrack
    program_goal: str
    final_attestation_result: str
    standards_basis: str

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def track_id(self) -> str:
        return self.track.track_id

    @property
    def professional_area(self) -> str:
        return self.profile.professional_area

    @property
    def qualification_level(self) -> str:
        return self.profile.qualification_level

    @property
    def parallel_education_note(self) -> str:
        return self.profile.parallel_education_note

    @property
    def audience_requirements(self) -> tuple[str, ...]:
        return self.profile.audience_requirements

    @property
    def additional_requirements(self) -> tuple[str, ...]:
        return self.profile.additional_requirements

    @property
    def entry_requirements(self) -> str:
        return self.profile.entry_requirements

    @property
    def professional_objects(self) -> tuple[str, ...]:
        return self.track.professional_objects

    @property
    def activity_types(self) -> tuple[str, ...]:
        return self.track.activity_types

    @property
    def labor_functions(self) -> tuple[LaborFunctionEntry, ...]:
        return self.track.labor_functions

    @property
    def activity_matrix(self) -> tuple[ActivityMatrixEntry, ...]:
        return self.track.activity_matrix


def configure_dynamic_registry(path: Path) -> None:
    global _dynamic_registry_path
    _dynamic_registry_path = path
    _dynamic_registry_path.parent.mkdir(parents=True, exist_ok=True)


def _load_dynamic_registry() -> dict[str, dict]:
    if not _dynamic_registry_path.exists():
        return {}
    try:
        return json.loads(_dynamic_registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dynamic_registry(registry: dict[str, dict]) -> None:
    _dynamic_registry_path.parent.mkdir(parents=True, exist_ok=True)
    _dynamic_registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _dynamic_profile_id(fgos_code: str) -> str:
    return f"auto_fgos_spo_{fgos_code.replace('.', '_')}"


def _infer_generic_qualification(course_name: str) -> str:
    text = course_name.lower()
    if any(keyword in text for keyword in ("backend", "node", "express", "web", "веб")):
        return "разработчик веб-приложений"
    if any(keyword in text for keyword in ("devops", "docker", "kubernetes", "инфраструктур")):
        return "специалист по автоматизации инфраструктуры"
    if any(keyword in text for keyword in ("тест", "qa", "quality")):
        return "специалист по тестированию программного обеспечения"
    if any(keyword in text for keyword in ("аналит", "system", "систем")):
        return "специалист по информационным системам"
    return "специалист в области информационных технологий"


def _normalize_extracted_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


_DYNAMIC_COMPETENCE_CODE_RE = re.compile(r"(?i)\b\u041f\u041a\s*(\d+\.\d+)\b")
_DYNAMIC_COMPETENCE_SPLIT_RE = re.compile(r"(?i)(?=\b\u041f\u041a\s*\d+\.\d+\b)")
_DYNAMIC_COMPETENCE_LEADING_RE = re.compile(r"(?is)\A\s*(\u041f\u041a\s*\d+\.\d+)\.?\s*(.*)\Z")
_DYNAMIC_COMPETENCE_STOP_RE = re.compile(
    r"(?is)("
    r"\b(?:fgos\.ru|www\.|http(?:s)?://|powered by tcpdf)\b"
    r"|\b(?:[IVX]+|\d+(?:\.\d+){1,3})\.\s+[A-ZА-ЯЁ]"
    r"|\b(?:\u041e\u041a\s*\d+"
    r"|\u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435"
    r"|\u041f\u0435\u0440\u0435\u0447\u0435\u043d\u044c"
    r"|\u041c\u0430\u0441\u0442\u0435\u0440\u0441\u043a\u0438\u0435"
    r"|\u041f\u043e\u043b\u0438\u0433\u043e\u043d\u044b"
    r"|\u0421\u0442\u0443\u0434\u0438\u0438"
    r"|\u0417\u0430\u043b\u044b)\b"
    r")"
)


def _sanitize_dynamic_competency_fragment(fragment: str) -> str | None:
    normalized = _normalize_extracted_line(fragment)
    if not normalized:
        return None

    match = _DYNAMIC_COMPETENCE_LEADING_RE.match(normalized)
    if not match:
        return None

    code = re.sub(r"\s+", " ", match.group(1).upper()).strip()
    text = match.group(2).strip()
    stop_match = _DYNAMIC_COMPETENCE_STOP_RE.search(text)
    if stop_match:
        text = text[: stop_match.start()]
    text = text.strip(" .;:,!?\t\r\n-–")
    if not text:
        return None
    return f"{code} {text}."


def _normalize_dynamic_competencies(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized_competencies: list[str] = []
    seen_codes: set[str] = set()

    for value in values:
        chunks = _DYNAMIC_COMPETENCE_SPLIT_RE.split(_normalize_extracted_line(str(value)))
        for chunk in chunks:
            sanitized = _sanitize_dynamic_competency_fragment(chunk)
            code_match = _DYNAMIC_COMPETENCE_CODE_RE.search(sanitized or "")
            code = code_match.group(1) if code_match else None
            if sanitized and sanitized not in normalized_competencies and code not in seen_codes:
                normalized_competencies.append(sanitized)
                if code:
                    seen_codes.add(code)

    return tuple(normalized_competencies)


def _extract_dynamic_competencies(extracted_text: str) -> tuple[str, ...]:
    if not extracted_text.strip():
        return ()

    lines = [_normalize_extracted_line(line) for line in extracted_text.splitlines()]
    competencies: list[str] = []
    current: str | None = None
    stop_pattern = re.compile(
        r"^(ОК\s*\d+|Уметь|Знать|Иметь практический опыт|Основной вид деятельности|Наименование квалификации|"
        r"Область профессиональной деятельности|Трудовые функции|В результате освоения|Практический опыт|Умения|Знания)\b",
        re.IGNORECASE,
    )
    competence_pattern = re.compile(r"^(ПК\s*\d+\.\d+)\.?\s*(.*)$", re.IGNORECASE)

    for line in lines:
        if not line:
            if current:
                competencies.append(current)
                current = None
            continue

        competence_match = competence_pattern.match(line)
        if competence_match:
            if current:
                competencies.append(current)
            code = re.sub(r"\s+", " ", competence_match.group(1).upper()).strip()
            text = competence_match.group(2).strip(" .;:")
            current = f"{code} {text}".strip()
            continue

        if current and stop_pattern.match(line):
            competencies.append(current)
            current = None
            continue

        if current:
            current = f"{current} {line}".strip()

    if current:
        competencies.append(current)

    normalized = _normalize_dynamic_competencies(competencies)
    if normalized:
        return normalized
    return _normalize_dynamic_competencies([extracted_text])


def extract_dynamic_competencies_from_text(extracted_text: str) -> tuple[str, ...]:
    return _extract_dynamic_competencies(extracted_text)


def _group_dynamic_competencies(competencies: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    grouped: list[tuple[str, list[str]]] = []
    for competence in competencies:
        match = re.match(r"ПК\s*(\d+)\.\d+", competence, flags=re.IGNORECASE)
        group_key = match.group(1) if match else str(len(grouped) + 1)
        existing = next((entry for entry in grouped if entry[0] == group_key), None)
        if existing is None:
            grouped.append((group_key, [competence]))
        else:
            existing[1].append(competence)
    return grouped


def _build_dynamic_labor_functions(course_theme: str, competencies: tuple[str, ...]) -> tuple[LaborFunctionEntry, ...]:
    grouped = _group_dynamic_competencies(competencies)
    if not grouped:
        return ()

    labor_functions: list[LaborFunctionEntry] = []
    for index, (group_key, group_competencies) in enumerate(grouped, start=1):
        labor_functions.append(
            LaborFunctionEntry(
                name=f"Группа профессиональных компетенций ПК {group_key}.x по тематике курса «{course_theme}».",
                code_level=f"A/{index:02d}.6 (ур. 6)",
                competencies=tuple(group_competencies),
            )
        )
    return tuple(labor_functions)


def _build_dynamic_activity_matrix(
    course_theme: str,
    labor_functions: tuple[LaborFunctionEntry, ...],
) -> tuple[ActivityMatrixEntry, ...]:
    if not labor_functions:
        return ()

    all_competencies = [competency for function in labor_functions for competency in function.competencies]
    labor_function_lines = [f"{function.code_level} {function.name}" for function in labor_functions]
    return (
        ActivityMatrixEntry(
            activity=f"Выполнение профессиональных задач по тематике курса «{course_theme}».",
            competencies=(
                "Профессиональные компетенции:\n"
                + "\n".join(all_competencies)
                + "\nТрудовые функции:\n"
                + "\n".join(labor_function_lines)
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Выполнение практических заданий по тематике курса в соответствии с профессиональными компетенциями.\n"
                "– Подготовка артефактов, подтверждающих освоение модулей программы."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Применять профильные инструменты и методы выполнения профессиональных задач.\n"
                "– Документировать результаты, обосновывать решения и воспроизводить полученные артефакты."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Основные подходы, инструменты и требования к качеству результатов по тематике курса.\n"
                "– Нормативные и методические основания реализации профессиональных задач."
            ),
        ),
    )


def _build_dynamic_profile(payload: dict) -> StandardProfile:
    qualification_title = payload.get("qualification_title") or "специалист в области информационных технологий"
    course_theme = payload.get("course_name") or "профильной тематике курса"
    dynamic_competencies = _normalize_dynamic_competencies(tuple(payload.get("competencies") or ()))
    dynamic_labor_functions = _build_dynamic_labor_functions(course_theme, dynamic_competencies)
    dynamic_activity_matrix = _build_dynamic_activity_matrix(course_theme, dynamic_labor_functions)
    track = StandardTrack(
        track_id=payload.get("track_id") or "generic",
        qualification_title=qualification_title,
        professional_objects=tuple(
            payload.get("professional_objects")
            or (
                "Программные и цифровые решения по тематике курса",
                "Документация, сценарии настройки и эксплуатационные материалы",
                "Прикладные и инфраструктурные сервисы, используемые в рамках курса",
            )
        ),
        activity_types=tuple(
            payload.get("activity_types")
            or (
                f"Проектирование и реализация решений по тематике курса «{course_theme}».",
                "Настройка, тестирование и сопровождение полученных результатов.",
            )
        ),
        labor_functions=dynamic_labor_functions or (
            LaborFunctionEntry(
                name=f"Подготовка и проектирование решений по тематике курса «{course_theme}».",
                code_level="A/01.6 (ур. 6)",
                competencies=(
                    "ПК 1.1 Анализировать требования и формировать структуру решения.",
                    "ПК 1.2 Подготавливать проектные и технические материалы по тематике курса.",
                ),
            ),
            LaborFunctionEntry(
                name=f"Разработка, настройка и интеграция решений по тематике курса «{course_theme}».",
                code_level="A/02.6 (ур. 6)",
                competencies=(
                    "ПК 2.1 Реализовывать и настраивать компоненты решения.",
                    "ПК 2.2 Выполнять интеграцию, отладку и контроль корректности работы.",
                ),
            ),
            LaborFunctionEntry(
                name=f"Проверка качества, сопровождение и совершенствование решений по тематике курса «{course_theme}».",
                code_level="A/03.6 (ур. 6)",
                competencies=(
                    "ПК 3.1 Проводить тестирование, анализ и устранение недостатков.",
                    "ПК 3.2 Готовить результаты, рекомендации и материалы сопровождения.",
                ),
            ),
        ),
        activity_matrix=dynamic_activity_matrix or (
            ActivityMatrixEntry(
                activity=f"Выполнение профессиональных задач по тематике курса «{course_theme}».",
                competencies=(
                    "Профессиональные компетенции:\n"
                    "ПК 1.1 Анализировать требования и проектировать решение.\n"
                    "ПК 2.1 Реализовывать и настраивать компоненты решения.\n"
                    "ПК 3.1 Проводить тестирование и сопровождение.\n"
                    "Трудовые функции:\n"
                    "A/01.6 Подготовка и проектирование решений.\n"
                    "A/02.6 Разработка, настройка и интеграция решений.\n"
                    "A/03.6 Проверка качества и сопровождение решений."
                ),
                practical_experience=(
                    "С учётом на ПОП ФГОС:\n"
                    "– Выполнение практических заданий по проектированию, настройке и сопровождению решений.\n"
                    "– Подготовка и применение инструментов, сервисов и документации по тематике курса."
                ),
                skills=(
                    "С учётом на ПОП ФГОС:\n"
                    "– Анализировать требования, настраивать решения и выполнять отладку.\n"
                    "– Документировать результаты, применять профильные инструменты и сопровождать решения."
                ),
                knowledge=(
                    "С учётом на ПОП ФГОС:\n"
                    "– Принципы проектирования, реализации, тестирования и сопровождения решений.\n"
                    "– Профильные технологии, инструменты и требования к качеству результатов."
                ),
            ),
        ),
    )

    return StandardProfile(
        profile_id=payload["profile_id"],
        fgos_code=payload["fgos_code"],
        title=payload["title"],
        order_title=payload["order_title"],
        source_url=payload["source_url"],
        professional_area=payload["professional_area"],
        qualification_level=payload["qualification_level"],
        parallel_education_note=payload["parallel_education_note"],
        audience_requirements=tuple(payload["audience_requirements"]),
        additional_requirements=tuple(payload["additional_requirements"]),
        entry_requirements=payload["entry_requirements"],
        tracks=(track,),
    )


PROGRAMMER_TRACK = StandardTrack(
    track_id="programmer",
    qualification_title="программист",
    professional_objects=(
        "Программные модули и программные компоненты",
        "Информационные системы и сервисы",
        "Интеграционные сценарии и прикладные решения",
        "Техническая и проектная документация",
        "Инструменты автоматизации разработки и контроля качества",
    ),
    activity_types=(
        "Разработка модулей программного обеспечения для компьютерных систем.",
        "Осуществление интеграции программных модулей.",
    ),
    labor_functions=(
        LaborFunctionEntry(
            name="Формализация требований и проектирование программных решений по тематике курса.",
            code_level="A/01.6 (ур. 6)",
            competencies=(
                "ПК 1.1 Формировать алгоритмы разработки программных модулей в соответствии с техническим заданием.",
                "ПК 1.2 Разрабатывать программные модули в соответствии с техническим заданием.",
                "ПК 2.1 Разрабатывать требования к программным модулям на основе анализа проектной и технической документации.",
            ),
        ),
        LaborFunctionEntry(
            name="Разработка, отладка и интеграция программных модулей.",
            code_level="A/02.6 (ур. 6)",
            competencies=(
                "ПК 1.3 Выполнять отладку программных модулей с использованием специализированных программных средств.",
                "ПК 2.2 Выполнять интеграцию модулей в программное обеспечение.",
                "ПК 2.3 Выполнять отладку программного модуля с использованием специализированных программных средств.",
            ),
        ),
        LaborFunctionEntry(
            name="Подготовка тестовых сценариев и контроль качества программных решений.",
            code_level="A/03.6 (ур. 6)",
            competencies=(
                "ПК 2.4 Осуществлять разработку тестовых наборов и тестовых сценариев для программного обеспечения.",
                "ПК 2.5 Производить инспектирование компонентов программного обеспечения на предмет соответствия стандартам кодирования.",
            ),
        ),
    ),
    activity_matrix=(
        ActivityMatrixEntry(
            activity="Разработка и интеграция программных решений по тематике курса.",
            competencies=(
                "Профессиональные компетенции:\n"
                "ПК 1.1 Формировать алгоритмы разработки программных модулей в соответствии с техническим заданием.\n"
                "ПК 1.2 Разрабатывать программные модули в соответствии с техническим заданием.\n"
                "ПК 1.3 Выполнять отладку программных модулей с использованием специализированных программных средств.\n"
                "ПК 2.1 Разрабатывать требования к программным модулям на основе анализа проектной и технической документации.\n"
                "ПК 2.4 Осуществлять разработку тестовых наборов и тестовых сценариев для программного обеспечения.\n"
                "Трудовые функции:\n"
                "A/01.6 Формализация требований и проектирование программных решений по тематике курса.\n"
                "A/02.6 Разработка, отладка и интеграция программных модулей.\n"
                "A/03.6 Подготовка тестовых сценариев и контроль качества программных решений."
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Проектирование структуры программного решения и подготовка алгоритмов реализации.\n"
                "– Разработка, отладка и интеграция программных модулей под задачи конкретного курса.\n"
                "– Подготовка тестовых сценариев, проведение проверки и фиксация результатов контроля качества."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Формализовать требования и декомпозировать задачи на программные модули.\n"
                "– Реализовывать программную логику, интеграционные сценарии и прикладные сервисы.\n"
                "– Проводить тестирование, анализировать результаты и улучшать программные решения по тематике курса."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Алгоритмизация, программирование и интеграция программных модулей.\n"
                "– Основы тестирования, отладки и контроля качества программных решений.\n"
                "– Средства разработки, сопровождения и документирования программного обеспечения."
            ),
        ),
    ),
)

TESTING_TRACK = StandardTrack(
    track_id="testing",
    qualification_title="специалист по тестированию в области информационных технологий",
    professional_objects=(
        "Программные продукты и сервисы",
        "Тестовые сценарии и наборы данных",
        "Средства верификации и валидации",
        "Артефакты контроля качества и отчётности",
    ),
    activity_types=("Ревьюирование программных продуктов.",),
    labor_functions=(
        LaborFunctionEntry(
            name="Ревьюирование программных продуктов.",
            code_level="A/01.6 (ур. 6)",
            competencies=(
                "ПК 3.1 Осуществлять ревьюирование программного кода в соответствии с технической документацией.",
                "ПК 3.2 Выполнять процесс измерения характеристик компонентов программного продукта.",
                "ПК 3.3 Производить исследование созданного программного кода с использованием специализированных программных средств с целью выявления ошибок и отклонения от алгоритма.",
                "ПК 3.4 Проводить сравнительный анализ программных продуктов и средств разработки.",
            ),
        ),
    ),
    activity_matrix=(
        ActivityMatrixEntry(
            activity="Оценка качества, ревьюирование и верификация решений по тематике курса.",
            competencies=(
                "Профессиональные компетенции:\n"
                "ПК 3.1 Осуществлять ревьюирование программного кода в соответствии с технической документацией.\n"
                "ПК 3.2 Выполнять процесс измерения характеристик компонентов программного продукта.\n"
                "ПК 3.3 Производить исследование созданного программного кода с использованием специализированных программных средств с целью выявления ошибок и отклонения от алгоритма.\n"
                "ПК 3.4 Проводить сравнительный анализ программных продуктов и средств разработки.\n"
                "Трудовые функции:\n"
                "A/01.6 Ревьюирование программных продуктов и контроль качества решений."
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Подготовка сценариев проверки и критериев оценки качества.\n"
                "– Проведение ревью, сравнительного анализа и фиксация результатов контроля.\n"
                "– Подготовка рекомендаций по устранению выявленных замечаний."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Формировать критерии качества и сценарии проверки.\n"
                "– Проводить ревью, анализировать дефекты и оценивать соответствие требованиям.\n"
                "– Подготавливать выводы, отчёты и рекомендации по улучшению решений."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Методы верификации и валидации программных продуктов.\n"
                "– Метрики качества ПО и документация тестирования.\n"
                "– Подходы к анализу, сравнению и экспертизе программных решений."
            ),
        ),
    ),
)

WEB_TRACK = StandardTrack(
    track_id="web",
    qualification_title="разработчик веб и мультимедийных приложений",
    professional_objects=(
        "Веб-приложения и мультимедийные приложения",
        "Серверные и клиентские технологии",
        "Информационные ресурсы и пользовательские интерфейсы",
        "Техническая документация и дизайн-макеты",
    ),
    activity_types=(
        "Проектирование, разработка и оптимизация веб-приложений.",
        "Разработка дизайна веб-приложений.",
    ),
    labor_functions=(
        LaborFunctionEntry(
            name="Проектирование, разработка и оптимизация веб-приложений.",
            code_level="A/01.6 (ур. 6)",
            competencies=(
                "ПК 9.1 Разрабатывать техническое задание на веб-приложение в соответствии с требованиями заказчика.",
                "ПК 9.2 Разрабатывать веб-приложение в соответствии с техническим заданием.",
                "ПК 9.6 Разрабатывать техническую документацию на веб-приложение.",
            ),
        ),
        LaborFunctionEntry(
            name="Разработка дизайна веб-приложений.",
            code_level="A/02.6 (ур. 6)",
            competencies=(
                "ПК 8.1 Разрабатывать дизайн-концепции веб-приложений в соответствии с корпоративным стилем заказчика.",
                "ПК 8.2 Формировать требования к дизайну веб-приложений на основе анализа предметной области и целевой аудитории.",
            ),
        ),
    ),
    activity_matrix=(
        ActivityMatrixEntry(
            activity="Проектирование и разработка веб-решений по тематике курса.",
            competencies=(
                "Профессиональные компетенции:\n"
                "ПК 8.1 Разрабатывать дизайн-концепции веб-приложений в соответствии с корпоративным стилем заказчика.\n"
                "ПК 8.2 Формировать требования к дизайну веб-приложений на основе анализа предметной области и целевой аудитории.\n"
                "ПК 9.1 Разрабатывать техническое задание на веб-приложение в соответствии с требованиями заказчика.\n"
                "ПК 9.2 Разрабатывать веб-приложение в соответствии с техническим заданием.\n"
                "ПК 9.6 Разрабатывать техническую документацию на веб-приложение.\n"
                "Трудовые функции:\n"
                "A/01.6 Проектирование, разработка и оптимизация веб-приложений.\n"
                "A/02.6 Разработка дизайна веб-приложений."
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Разработка структуры веб-приложения и пользовательских сценариев.\n"
                "– Подготовка интерфейсов, страниц и мультимедийных компонентов.\n"
                "– Реализация и документирование веб-решений под задачу курса."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Анализировать требования заказчика и целевой аудитории.\n"
                "– Проектировать интерфейсы и веб-архитектуру.\n"
                "– Разрабатывать и документировать веб-приложение."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Основы веб-технологий и архитектуры веб-приложений.\n"
                "– Подходы к проектированию пользовательских интерфейсов.\n"
                "– Правила подготовки технической документации на веб-решения."
            ),
        ),
    ),
)

INFORMATION_SYSTEMS_TRACK = StandardTrack(
    track_id="information_systems",
    qualification_title="специалист по информационным системам",
    professional_objects=(
        "Информационные системы и прикладные сервисы",
        "Пользовательские процессы и прикладные сценарии",
        "Проектная и эксплуатационная документация",
        "Интеграционные и аналитические материалы",
    ),
    activity_types=(
        "Осуществление интеграции программных модулей.",
        "Сопровождение информационных систем.",
    ),
    labor_functions=(
        LaborFunctionEntry(
            name="Сопровождение и развитие информационных систем.",
            code_level="A/01.6 (ур. 6)",
            competencies=(
                "ПК 5.1 Собирать исходные данные для разработки проектной документации на информационную систему.",
                "ПК 5.7 Производить оценку информационной системы для выявления возможности её модернизации.",
            ),
        ),
        LaborFunctionEntry(
            name="Соотнесение бизнес-требований и программной реализации.",
            code_level="A/02.6 (ур. 6)",
            competencies=(
                "ПК 6.1 Разрабатывать техническое задание на сопровождение информационной системы.",
                "ПК 6.4 Оценивать качество и надёжность функционирования информационной системы.",
            ),
        ),
    ),
    activity_matrix=(
        ActivityMatrixEntry(
            activity="Анализ, сопровождение и развитие информационных решений по тематике курса.",
            competencies=(
                "Профессиональные компетенции:\n"
                "ПК 5.1 Собирать исходные данные для разработки проектной документации на информационную систему.\n"
                "ПК 5.7 Производить оценку информационной системы для выявления возможности её модернизации.\n"
                "ПК 6.1 Разрабатывать техническое задание на сопровождение информационной системы.\n"
                "ПК 6.4 Оценивать качество и надёжность функционирования информационной системы.\n"
                "Трудовые функции:\n"
                "A/01.6 Сопровождение и развитие информационных систем.\n"
                "A/02.6 Соотнесение бизнес-требований и программной реализации."
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Анализ требований и подготовка проектной документации.\n"
                "– Сопровождение и модернизация информационных систем под прикладные задачи курса.\n"
                "– Оценка качества функционирования и подготовка предложений по развитию."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Описывать требования и формировать технические задания.\n"
                "– Оценивать текущее состояние системы и планировать изменения.\n"
                "– Сопровождать внедрение и развитие информационных решений."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Жизненный цикл информационных систем и проектная документация.\n"
                "– Подходы к сопровождению и модернизации прикладных решений.\n"
                "– Методы оценки качества и надёжности функционирования систем."
            ),
        ),
    ),
)

DEVOPS_INFRASTRUCTURE_TRACK = StandardTrack(
    track_id="devops_infrastructure",
    qualification_title="специалист по информационным системам",
    professional_objects=(
        "Скрипты автоматизации и эксплуатационные утилиты на Python",
        "Контейнеризированные приложения и сервисы инфраструктуры",
        "Конвейеры непрерывной интеграции и поставки",
        "Инфраструктура как код, конфигурации и оркестрация",
        "Средства мониторинга, журналирования и эксплуатационной аналитики",
    ),
    activity_types=(
        "Конфигурирование, управление и мониторинг ИТ-инфраструктуры.",
        "Автоматизация процессов развертывания, сопровождения и эксплуатации программного обеспечения.",
    ),
    labor_functions=(
        LaborFunctionEntry(
            name="Автоматизация инфраструктурных и эксплуатационных процедур средствами Python и системного окружения.",
            code_level="A/01.6 (ур. 6)",
            competencies=(
                "ПК 1.1 Разрабатывать скрипты автоматизации для администрирования, сопровождения и интеграции сервисов.",
                "ПК 1.2 Применять Python для работы с файловой системой, сетевыми ресурсами, API и системными процессами.",
                "ПК 1.3 Выполнять настройку и отладку скриптовых решений для инфраструктурных задач.",
            ),
        ),
        LaborFunctionEntry(
            name="Настройка контейнеризации, конвейеров CI/CD и управления конфигурациями.",
            code_level="A/02.6 (ур. 6)",
            competencies=(
                "ПК 2.1 Готовить конфигурации Docker, Docker Compose и Kubernetes для развертывания сервисов.",
                "ПК 2.2 Выполнять настройку конвейеров CI/CD и автоматизированной доставки изменений.",
                "ПК 2.3 Применять Terraform и Ansible для управления инфраструктурой и конфигурациями.",
            ),
        ),
        LaborFunctionEntry(
            name="Мониторинг, журналирование и обеспечение надежности работы инфраструктуры и прикладных сервисов.",
            code_level="A/03.6 (ур. 6)",
            competencies=(
                "ПК 3.1 Организовывать мониторинг, сбор метрик и логов для ИТ-сервисов.",
                "ПК 3.2 Анализировать инциденты, устранять эксплуатационные проблемы и снижать риски отказов.",
                "ПК 3.3 Документировать инфраструктурные решения, сценарии восстановления и регламенты сопровождения.",
            ),
        ),
    ),
    activity_matrix=(
        ActivityMatrixEntry(
            activity="Автоматизация инфраструктуры, развертывания и сопровождения сервисов по тематике курса.",
            competencies=(
                "Профессиональные компетенции:\n"
                "ПК 1.1 Разрабатывать скрипты автоматизации для администрирования, сопровождения и интеграции сервисов.\n"
                "ПК 1.2 Применять Python для работы с файловой системой, сетевыми ресурсами, API и системными процессами.\n"
                "ПК 1.3 Выполнять настройку и отладку скриптовых решений для инфраструктурных задач.\n"
                "ПК 2.1 Готовить конфигурации Docker, Docker Compose и Kubernetes для развертывания сервисов.\n"
                "ПК 2.2 Выполнять настройку конвейеров CI/CD и автоматизированной доставки изменений.\n"
                "ПК 2.3 Применять Terraform и Ansible для управления инфраструктурой и конфигурациями.\n"
                "ПК 3.1 Организовывать мониторинг, сбор метрик и логов для ИТ-сервисов.\n"
                "ПК 3.2 Анализировать инциденты, устранять эксплуатационные проблемы и снижать риски отказов.\n"
                "Трудовые функции:\n"
                "A/01.6 Автоматизация инфраструктурных и эксплуатационных процедур средствами Python и системного окружения.\n"
                "A/02.6 Настройка контейнеризации, конвейеров CI/CD и управления конфигурациями.\n"
                "A/03.6 Мониторинг, журналирование и обеспечение надежности работы инфраструктуры и прикладных сервисов."
            ),
            practical_experience=(
                "С учётом на ПОП ФГОС:\n"
                "– Разработка Python-скриптов для SSH-администрирования, обработки конфигураций и автоматизации рутинных операций.\n"
                "– Подготовка Docker-образов, файлов Docker Compose, Kubernetes-манифестов и сценариев развертывания.\n"
                "– Настройка CI/CD-процессов, инфраструктуры как кода, конфигурационного управления и эксплуатационного контроля.\n"
                "– Организация мониторинга, журналирования, диагностики и документирования инфраструктурных решений."
            ),
            skills=(
                "С учётом на ПОП ФГОС:\n"
                "– Автоматизировать инфраструктурные операции на Python и использовать системные интерфейсы Linux.\n"
                "– Подготавливать и сопровождать контейнеризированные среды, Kubernetes-кластеры и конфигурации сервисов.\n"
                "– Проектировать CI/CD-конвейеры, управлять конфигурациями и описывать инфраструктуру в коде.\n"
                "– Выявлять и устранять эксплуатационные проблемы по метрикам, журналам и результатам мониторинга."
            ),
            knowledge=(
                "С учётом на ПОП ФГОС:\n"
                "– Python для автоматизации администрирования, сетевого взаимодействия, API и системных задач.\n"
                "– Принципы DevOps, контейнеризации, оркестрации, непрерывной интеграции и непрерывной поставки.\n"
                "– Подходы к управлению инфраструктурой как кодом, конфигурациями, секретами и средами выполнения.\n"
                "– Методы мониторинга, журналирования, обеспечения отказоустойчивости и сопровождения ИТ-инфраструктуры."
            ),
        ),
    ),
)

FGOS_09_02_07_PROFILE = StandardProfile(
    profile_id=DEFAULT_STANDARD_PROFILE_ID,
    fgos_code="09.02.07",
    title="ФГОС СПО 09.02.07 «Информационные системы и программирование»",
    order_title="приказ Минобрнауки России от 09.12.2016 № 1547 (ред. от 17.12.2020)",
    source_url="https://fgos.ru/fgos/fgos-09-02-07-informacionnye-sistemy-i-programmirovanie-1547/",
    professional_area="06 Связь, информационные и коммуникационные технологии.",
    qualification_level="Уровень квалификации определяется выбранным видом деятельности и квалификацией специалиста среднего звена в рамках ФГОС СПО 09.02.07.",
    parallel_education_note=(
        "Лицам, осваивающим программу параллельно с получением СПО и (или) ВО, документ о квалификации "
        "выдаётся одновременно с получением соответствующего документа об образовании и квалификации "
        "по базовой образовательной программе."
    ),
    audience_requirements=(
        "Лица, имеющие среднее профессиональное и (или) высшее образование.",
        "Лица, получающие среднее профессиональное и (или) высшее образование.",
    ),
    additional_requirements=(
        "Базовые навыки работы с компьютером, браузером, файловой системой и офисными приложениями.",
        "Наличие доступа к сети Интернет и готовность использовать электронное обучение и дистанционные образовательные технологии.",
    ),
    entry_requirements="Вступительные испытания не предусмотрены.",
    tracks=(
        PROGRAMMER_TRACK,
        TESTING_TRACK,
        WEB_TRACK,
        INFORMATION_SYSTEMS_TRACK,
    ),
)

FGOS_09_02_11_PROFILE = StandardProfile(
    profile_id="fgos_spo_09_02_11",
    fgos_code="09.02.11",
    title="ФГОС СПО 09.02.11 «Разработка и управление программным обеспечением»",
    order_title="приказ Минпросвещения России от 24.02.2025 № 138",
    source_url="https://publication.pravo.gov.ru/document/0001202503310008",
    professional_area="06 Связь, информационные и коммуникационные технологии.",
    qualification_level="Уровень квалификации определяется выбранным видом деятельности и квалификацией специалиста среднего звена в рамках ФГОС СПО 09.02.11.",
    parallel_education_note=(
        "Лицам, осваивающим программу параллельно с получением СПО и (или) ВО, документ о квалификации "
        "выдаётся одновременно с получением соответствующего документа об образовании и квалификации "
        "по базовой образовательной программе."
    ),
    audience_requirements=(
        "Лица, имеющие среднее профессиональное и (или) высшее образование.",
        "Лица, получающие среднее профессиональное и (или) высшее образование.",
    ),
    additional_requirements=(
        "Базовые навыки работы с операционными системами, командной строкой, браузером и офисными приложениями.",
        "Наличие доступа к сети Интернет и готовность использовать электронное обучение и дистанционные образовательные технологии.",
    ),
    entry_requirements="Вступительные испытания не предусмотрены.",
    tracks=(DEVOPS_INFRASTRUCTURE_TRACK,),
)

STANDARD_PROFILES = {
    FGOS_09_02_07_PROFILE.profile_id: FGOS_09_02_07_PROFILE,
    FGOS_09_02_11_PROFILE.profile_id: FGOS_09_02_11_PROFILE,
}
FGOS_CODE_TO_PROFILE_ID = {profile.fgos_code: profile.profile_id for profile in STANDARD_PROFILES.values()}


def _normalize_profile_id(profile_id: str | None) -> str:
    raw = (profile_id or DEFAULT_STANDARD_PROFILE_ID).strip() or DEFAULT_STANDARD_PROFILE_ID
    return LEGACY_PROFILE_IDS.get(raw, raw)


def get_standard_profile(profile_id: str | None) -> StandardProfile:
    resolved_id = _normalize_profile_id(profile_id)
    profile = STANDARD_PROFILES.get(resolved_id)
    if profile is None:
        dynamic = _load_dynamic_registry().get(resolved_id)
        if dynamic is not None:
            profile = _build_dynamic_profile(dynamic)
    if profile is None:
        supported = ", ".join(sorted(STANDARD_PROFILES))
        raise DraftValidationError(
            f"Неподдерживаемый профиль стандартов: {resolved_id}. Доступные профили: {supported}."
        )
    return profile


def extract_fgos_code(reference: str | None) -> str | None:
    if not reference:
        return None
    match = re.search(r"(\d{2}\.\d{2}\.\d{2})", reference)
    if match:
        return match.group(1)
    dashed = re.search(r"(\d{2})-(\d{2})-(\d{2})", reference)
    if dashed:
        return ".".join(dashed.groups())
    digits_only = re.search(r"(\d{6})", reference)
    if digits_only:
        raw = digits_only.group(1)
        return f"{raw[:2]}.{raw[2:4]}.{raw[4:6]}"
    return None


def get_standard_profile_by_reference(fgos_code: str | None = None, fgos_url: str | None = None) -> StandardProfile | None:
    resolved_code = extract_fgos_code(fgos_code) or extract_fgos_code(fgos_url)
    if not resolved_code:
        return None
    profile_id = FGOS_CODE_TO_PROFILE_ID.get(resolved_code)
    if profile_id is None:
        for payload in _load_dynamic_registry().values():
            if payload.get("fgos_code") == resolved_code:
                return _build_dynamic_profile(payload)
        return None
    return STANDARD_PROFILES[profile_id]


def register_dynamic_profile(
    *,
    fgos_code: str,
    source_url: str,
    title: str | None = None,
    order_title: str | None = None,
    course_name: str = "",
    professional_area: str = "",
    training_goal: str = "",
    brief_description: str = "",
    extracted_text: str = "",
) -> StandardProfile:
    profile_id = _dynamic_profile_id(fgos_code)
    registry = _load_dynamic_registry()
    payload = registry.get(profile_id) or {
        "profile_id": profile_id,
        "fgos_code": fgos_code,
        "title": title or f"ФГОС СПО {fgos_code}",
        "order_title": order_title or "Реквизиты приказа уточняются по представленному стандарту.",
        "source_url": source_url,
        "professional_area": "Область профессиональной деятельности определяется тематикой курса и представленным ФГОС.",
        "qualification_level": f"Квалификационный профиль программы определяется ФГОС {fgos_code} и содержанием курса.",
        "parallel_education_note": (
            "При освоении программы параллельно с получением СПО и (или) ВО документ о квалификации "
            "выдаётся после получения соответствующего документа об образовании."
        ),
        "audience_requirements": [
            "Лица, имеющие среднее профессиональное и (или) высшее образование.",
            "Лица, получающие среднее профессиональное и (или) высшее образование.",
        ],
        "additional_requirements": [
            "Базовые навыки работы с компьютером, браузером и профильным программным обеспечением.",
            "Наличие доступа к сети Интернет и готовность использовать электронное обучение и дистанционные образовательные технологии.",
        ],
        "entry_requirements": "Вступительные испытания не предусмотрены.",
        "track_id": "generic",
        "qualification_title": _infer_generic_qualification(course_name),
    }
    if source_url:
        payload["source_url"] = source_url
    if title:
        payload["title"] = title
    if order_title:
        payload["order_title"] = order_title
    if course_name:
        payload["qualification_title"] = _infer_generic_qualification(course_name)
        payload["course_name"] = course_name
    if professional_area.strip():
        payload["professional_area"] = professional_area.strip()
    elif brief_description.strip() or training_goal.strip():
        payload["professional_area"] = brief_description.strip() or training_goal.strip()
    extracted_competencies = list(_extract_dynamic_competencies(extracted_text))
    if extracted_competencies:
        payload["competencies"] = extracted_competencies
    else:
        sanitized_competencies = _normalize_dynamic_competencies(tuple(payload.get("competencies") or ()))
        if sanitized_competencies:
            payload["competencies"] = list(sanitized_competencies)
    registry[profile_id] = payload
    _save_dynamic_registry(registry)
    return _build_dynamic_profile(payload)


def detect_track_from_text(
    profile: StandardProfile,
    course_name: str = "",
    professional_area: str = "",
    training_goal: str = "",
    brief_description: str = "",
    module_names: tuple[str, ...] = (),
    module_summaries: tuple[str, ...] = (),
) -> str:
    haystack = " ".join(
        [
            course_name,
            professional_area,
            training_goal,
            brief_description,
            " ".join(module_names),
            " ".join(module_summaries),
        ]
    ).lower()
    supported_track_ids = {track.track_id for track in profile.tracks}

    if (
        "devops_infrastructure" in supported_track_ids
        and any(
            keyword in haystack
            for keyword in (
                "devops",
                "docker",
                "kubernetes",
                "terraform",
                "ansible",
                "ci/cd",
                "cicd",
                "инфраструктур",
                "контейнер",
                "оркестрац",
                "мониторинг",
            )
        )
    ):
        return "devops_infrastructure"
    if "programmer" in supported_track_ids and any(
        keyword in haystack for keyword in ("prompt", "промпт", "llm", "ии", "ai", "автоматизац")
    ):
        return "programmer"
    if "web" in supported_track_ids and any(
        keyword in haystack for keyword in ("web", "веб", "django", "frontend", "backend", "fastapi", "flask", "site", "сайт", "api")
    ):
        return "web"
    if "testing" in supported_track_ids and any(
        keyword in haystack for keyword in ("тест", "qa", "quality", "вериф", "ревью", "контроль качества")
    ):
        return "testing"
    if "information_systems" in supported_track_ids and any(
        keyword in haystack for keyword in ("информационн", "integration", "интеграц", "система", "system")
    ):
        return "information_systems"
    if len(profile.tracks) == 1:
        return profile.tracks[0].track_id
    return "programmer" if "programmer" in supported_track_ids else profile.tracks[0].track_id


def _detect_track(seed: CourseSeedRequest, profile: StandardProfile) -> str:
    haystack = " ".join(
        [
            seed.course_name,
            seed.professional_area,
            seed.training_goal,
            seed.brief_description,
            " ".join(module.name for module in seed.modules_seed),
            " ".join(module.summary for module in seed.modules_seed),
        ]
    ).lower()
    supported_track_ids = {track.track_id for track in profile.tracks}

    if (
        "devops_infrastructure" in supported_track_ids
        and any(
            keyword in haystack
            for keyword in (
                "devops",
                "docker",
                "kubernetes",
                "terraform",
                "ansible",
                "ci/cd",
                "cicd",
                "инфраструктур",
                "контейнер",
                "оркестрац",
                "мониторинг",
            )
        )
    ):
        return "devops_infrastructure"
    if "programmer" in supported_track_ids and any(
        keyword in haystack for keyword in ("prompt", "промпт", "llm", "ии", "ai", "автоматизац")
    ):
        return "programmer"
    if "web" in supported_track_ids and any(
        keyword in haystack for keyword in ("web", "веб", "django", "frontend", "backend", "fastapi", "flask", "site", "сайт", "api")
    ):
        return "web"
    if "testing" in supported_track_ids and any(
        keyword in haystack for keyword in ("тест", "qa", "quality", "вериф", "ревью", "контроль качества")
    ):
        return "testing"
    if "information_systems" in supported_track_ids and any(
        keyword in haystack for keyword in ("информационн", "integration", "интеграц", "система", "system")
    ):
        return "information_systems"
    if len(profile.tracks) == 1:
        return profile.tracks[0].track_id
    return "programmer" if "programmer" in supported_track_ids else profile.tracks[0].track_id


def resolve_standard_profile(seed: CourseSeedRequest) -> ResolvedStandardProfile:
    profile = get_standard_profile(seed.constraints.standard_profile_id)
    track_id = (seed.constraints.standard_track_id or "").strip().lower() or _detect_track(seed, profile)
    track = profile.get_track(track_id)

    course_theme = seed.course_name.strip()
    program_goal = (
        "Цель реализации программы — формирование и совершенствование профессиональных компетенций, "
        f"соотнесённых с квалификацией «{track.qualification_title}» и видами деятельности, предусмотренными "
        f"{profile.title} ({profile.order_title}), по тематике курса "
        f"«{course_theme}»."
    )
    final_attestation_result = (
        "В результате освоения программы и успешного прохождения итоговой аттестации слушателю выдается "
        "документ о квалификации по дополнительной профессиональной программе. "
        f"Квалификационный профиль программы соотнесён с направлением «{track.qualification_title}», "
        f"тематический профиль — «{course_theme}»."
    )

    return ResolvedStandardProfile(
        profile=profile,
        track=track,
        program_goal=program_goal,
        final_attestation_result=final_attestation_result,
        standards_basis=profile.compose_standards_basis(track, seed.constraints.standards),
    )
