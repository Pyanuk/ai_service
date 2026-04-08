from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.shared import Pt

from app.config import Settings
from app.schemas.draft import CourseDraft


class DocumentBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def template_available(self) -> bool:
        return self._settings.template_path.exists()

    def build_document(self, draft: CourseDraft) -> Path:
        if self._settings.template_path.exists():
            return self._build_document_from_template(draft)
        return self._build_document_fallback(draft)

    def _build_document_from_template(self, draft: CourseDraft) -> Path:
        document = Document(self._settings.template_path)

        self._fill_template_title(document, draft)
        self._fill_template_general(document, draft)
        self._fill_template_tables(document, draft)
        self._fill_template_calendar_intro(document, draft)
        self._fill_template_working_programs(document, draft)
        self._fill_template_organizational(document, draft)
        self._fill_template_assessment(document, draft)
        self._fill_template_signatures(document, draft)

        file_name = f"{self._slugify(draft.program_card.course_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        path = self._settings.output_dir / file_name
        document.save(path)
        return path

    def _build_document_fallback(self, draft: CourseDraft) -> Path:
        document = Document()
        style = document.styles["Normal"]
        style.font.name = "Times New Roman"
        style.font.size = Pt(12)

        self._build_title(document, draft)
        self._build_general_characteristics(document, draft)
        self._build_labor_functions_table(document, draft)
        self._build_activity_matrix_table(document, draft)
        self._build_study_plan_table(document, draft)
        self._build_working_programs_section(document, draft)
        self._build_calendar_variants_section(document, draft)
        self._build_organizational_section(document, draft)
        self._build_assessment_section(document, draft)
        self._build_resources_tables(document, draft)
        self._build_signatures(document, draft)

        file_name = f"{self._slugify(draft.program_card.course_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        path = self._settings.output_dir / file_name
        document.save(path)
        return path

    def _fill_template_title(self, document: Document, draft: CourseDraft) -> None:
        self._set_paragraph_text(document, "УТВЕРЖДАЮ", "УТВЕРЖДАЮ")
        self._set_paragraph_text(document, "Генеральный директор", draft.document_meta.approval_position)
        self._set_paragraph_text(document, "_______________ / Е. А. Шимбирева", f"_______________ / {draft.document_meta.approval_name}")
        self._set_paragraph_text(document, "«___» ____________ 2025 г.", draft.document_meta.approval_date)

        paragraph_0 = document.paragraphs[0]
        paragraph_0.text = self._format_organization_name(draft.document_meta.organization_name)
        self._set_paragraph_text(document, "ПРОГРАММА ПРОФЕССИОНАЛЬНОЙ ПЕРЕПОДГОТОВКИ", draft.program_card.program_type.upper())
        self._set_paragraph_text(
            document,
            "«РАЗРАБОТКА WEB-ПРИЛОЖЕНИЙ НА PYTHON (DJANGO)»",
            f"«{draft.program_card.course_name_upper}»",
        )
        self._set_paragraph_text(document, "Москва, 2025", f"{draft.document_meta.city}, {draft.document_meta.document_year}")

    def _fill_template_general(self, document: Document, draft: CourseDraft) -> None:
        gc = draft.general_characteristics
        self._set_paragraph_text(
            document,
            "Цель реализации программы — формирование у обучающихся профессиональных компетенций, необходимых для выполнения трудовых функций в области backend-разработки веб-приложений с использованием языка Python и фреймворка Django, в соответствии с ПС 06.035 «Разработчик WEB и мультимедийных приложений». (приказ Минтруда России от 18.01.2017 № 44н) и с учётом ФГОС СПО 09.02.09 Веб-разработка (Приказ Минпросвещения России от 21.11.2023 N 879).",
            gc.program_goal,
        )
        self._set_paragraph_text(document, "06.035 «Разработчик WEB и мультимедийных приложений».", self._ensure_period(gc.professional_area))
        self._replace_block_between_texts(
            document,
            "б) Объекты профессиональной деятельности",
            "в) Виды профессиональной деятельности",
            [self._ensure_period(item, use_semicolon=True, is_last=index == len(gc.professional_objects) - 1) for index, item in enumerate(gc.professional_objects)],
        )
        self._replace_block_between_texts(
            document,
            "в) Виды профессиональной деятельности",
            "г) Уровень квалификации в соответствии с профессиональным стандартом",
            [self._ensure_period(item) for item in gc.activity_types],
        )
        self._set_paragraph_text(document, "5 уровень квалификации.", self._ensure_period(gc.qualification_level))
        self._set_paragraph_text(
            document,
            "В результате освоения программы слушатель должен овладеть следующими трудовыми функциями (в соответствии с профессиональным стандартом) и/или профессиональными компетенциями (в соответствии с ФГОС):",
            "В результате освоения программы слушатель должен овладеть следующими трудовыми функциями (в соответствии с профессиональным стандартом) и/или профессиональными компетенциями (в соответствии с ФГОС):",
        )
        self._replace_block_between_texts(
            document,
            "К освоению программы допускаются:",
            "Дополнительные требования:",
            [self._ensure_period(item, use_semicolon=True, is_last=index == len(gc.audience_requirements) - 1) for index, item in enumerate(gc.audience_requirements)],
        )
        self._replace_block_between_texts(
            document,
            "Дополнительные требования:",
            "1.5. Объем программы",
            [*map(self._ensure_period, gc.additional_requirements), self._ensure_period(gc.entry_requirements)],
        )
        self._set_paragraph_text(document, "256 академических часов.", self._academic_hours_total_phrase(draft.program_card.hours))
        self._set_paragraph_text(
            document,
            "Заочная с применением электронного обучения и (или) дистанционных образовательных технологий.",
            self._ensure_period(gc.education_form),
        )
        self._set_paragraph_text(
            document,
            "В результате освоения программы и успешной сдачи итоговой аттестации слушателю выдается диплом о профессиональной переподготовке (документ о квалификации) с указанием квалификации: специалист по тестированию программного обеспечения (уровень квалификации 5 по ПС 06.035).",
            gc.final_attestation_result,
        )
        self._set_paragraph_text(
            document,
            "Лицам, осваивающим программу параллельно с получением СПО и (или) ВО, диплом о профпереподготовке выдается одновременно с получением соответствующего документа об образовании и о квалификации по базовой программе.",
            gc.parallel_education_note,
        )
        self._set_paragraph_text(
            document,
            "Программа разработана с учётом требований Профессионального стандарта 06.035 «Разработчик WEB и мультимедийных приложений». (приказ Минтруда России от 18.01.2017 № 44н), а также ФГОС СПО 09.02.09 Веб-разработка (Приказ Минпросвещения России от 21.11.2023 N 879).",
            gc.standards_basis,
        )

    def _fill_template_tables(self, document: Document, draft: CourseDraft) -> None:
        self._fill_labor_functions_table(document.tables[0], draft)
        self._fill_activity_matrix_table(document.tables[1], draft)
        self._fill_study_plan_table(document.tables[2], draft)
        for index, variant in enumerate(draft.calendar_variants, start=3):
            self._fill_calendar_variant_table(document.tables[index], variant, draft)
        self._fill_thematic_plan_table(document.tables[8], draft)
        self._fill_resources_table(document.tables[9], draft.facilities)
        self._fill_resources_table(document.tables[10], draft.digital_resources)

    def _fill_template_calendar_intro(self, document: Document, draft: CourseDraft) -> None:
        gc = draft.general_characteristics
        self._set_paragraph_text(
            document,
            "Все варианты соответствуют объему программы (256 академических часов) и обеспечивают достижение планируемых результатов. Выбор варианта осуществляется на основании заявления слушателя и фиксируется в приказе о зачислении.",
            gc.calendar_variants_intro_2,
        )
        variant_indices = [index for index, paragraph in enumerate(document.paragraphs) if paragraph.text.strip().startswith("Вариант ")]
        for idx, paragraph_index in enumerate(variant_indices[: len(draft.calendar_variants)]):
            document.paragraphs[paragraph_index].text = draft.calendar_variants[idx].description
        self._set_paragraph_text(
            document,
            "Учебно-тематический план программы профессиональной переподготовки",
            f"Учебно-тематический план {draft.program_card.program_type.lower()}",
        )

    def _fill_template_working_programs(self, document: Document, draft: CourseDraft) -> None:
        self._replace_block_between_texts(
            document,
            "2.4. Рабочие учебные программы дисциплин/модулей",
            "2.5. Организационно-педагогические условия реализации программы",
            self._split_block(draft.working_programs_block),
        )

    def _fill_template_organizational(self, document: Document, draft: CourseDraft) -> None:
        self._replace_block_between_texts(
            document,
            "2.5. Организационно-педагогические условия реализации программы",
            "ОЦЕНКА КАЧЕСТВА ОСВОЕНИЯ ПРОГРАММЫ",
            self._split_block(draft.organizational_conditions_block),
        )
        self._replace_block_between_texts(
            document,
            "ОЦЕНКА КАЧЕСТВА ОСВОЕНИЯ ПРОГРАММЫ",
            "3.1. Форма текущего контроля",
            [
                "Оценка качества освоения программы включает:",
                "Текущий контроль – проверка выполнения практических заданий.",
                "Промежуточную аттестацию – зачет.",
                "Итоговую аттестацию – экзамен.",
            ],
        )

    def _fill_template_assessment(self, document: Document, draft: CourseDraft) -> None:
        block = draft.assessment_block
        self._replace_block_between_texts(document, "3.1. Форма текущего контроля", "3.2. Форма промежуточной аттестации", self._split_block(block.current_control_block))
        self._replace_block_between_texts(document, "3.2. Форма промежуточной аттестации", "3.3. Итоговая аттестация", self._split_block(block.intermediate_attestation_block))
        self._replace_block_between_texts(document, "3.3. Итоговая аттестация", "3.3.1. Форма и цели", self._split_block(block.final_attestation_intro_block))
        self._replace_block_between_texts(document, "3.3.1. Форма и цели", "3.3.2. Требования к портфолио", self._split_block(block.final_attestation_form_and_goals_block))
        self._replace_block_between_texts(document, "3.3.2. Требования к портфолио", "3.3.3. Порядок проведения", self._split_block(block.portfolio_requirements_block))
        self._replace_block_between_texts(document, "3.3.3. Порядок проведения", "3.3.4. Структура доклада слушателя", self._split_block(block.attestation_procedure_block))
        self._replace_block_between_texts(document, "3.3.4. Структура доклада слушателя", "3.3.5. Примерные вопросы комиссии", self._split_block(block.report_structure_block))
        self._replace_block_between_texts(document, "3.3.5. Примерные вопросы комиссии", "3.3.6. Результаты и пересдача", self._split_block(block.commission_questions_block))
        self._replace_block_between_texts(document, "3.3.6. Результаты и пересдача", "3.4. Критерии оценки итогового экзамена", self._split_block(block.results_and_retake_block))
        self._replace_block_between_texts(document, "3.4. Критерии оценки итогового экзамена", "СОСТАВИТЕЛЬИ ПРОГРАММЫ", self._split_block(block.exam_grading_criteria_block))

    def _fill_template_signatures(self, document: Document, draft: CourseDraft) -> None:
        teacher_table = document.tables[11]
        teacher_table.rows[0].cells[0].text = draft.signatures.teacher_position
        teacher_table.rows[0].cells[1].text = draft.signatures.teacher_signature_line
        teacher_table.rows[0].cells[2].text = draft.signatures.teacher_name

        manager_table = document.tables[12]
        manager_table.rows[0].cells[0].text = draft.signatures.program_manager_position
        manager_table.rows[0].cells[1].text = draft.signatures.program_manager_signature_line
        manager_table.rows[0].cells[2].text = draft.signatures.program_manager_name

    def _build_title(self, document: Document, draft: CourseDraft) -> None:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(draft.document_meta.organization_name.upper()).bold = True

        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(draft.program_card.program_type.upper()).bold = True

        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(draft.program_card.course_name_upper).bold = True

        for line in (
            f"Объём программы: {draft.program_card.hours} академических часов",
            f"Форма обучения: {draft.program_card.format}",
            f"{draft.document_meta.city}, {draft.document_meta.document_year}",
        ):
            p = document.add_paragraph(line)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _build_general_characteristics(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("1. Общая характеристика программы")
        gc = draft.general_characteristics
        for line in [
            f"Цель программы: {gc.program_goal}",
            f"Профессиональная область: {gc.professional_area}",
            "Объекты профессиональной деятельности:",
            *[f"- {item}" for item in gc.professional_objects],
            "Виды деятельности:",
            *[f"- {item}" for item in gc.activity_types],
            f"Требования к слушателям: {'; '.join(gc.audience_requirements)}",
            f"Дополнительные требования: {'; '.join(gc.additional_requirements)}",
            f"Итоговая аттестация: {gc.final_attestation_result}",
            gc.standards_basis,
        ]:
            self._add_non_empty_paragraph(document, line)

    def _build_labor_functions_table(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("2.1. Трудовые функции")
        table = document.add_table(rows=1, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        header = table.rows[0].cells
        header[0].text = "Наименование трудовой функции"
        header[1].text = "Код / уровень"
        header[2].text = "Компетенции"
        for item in draft.labor_functions:
            row = table.add_row().cells
            row[0].text = item.name
            row[1].text = item.code_level
            row[2].text = "\n".join(item.competencies)

    def _build_activity_matrix_table(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("2.2. Матрица деятельности")
        table = document.add_table(rows=1, cols=5)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        header = table.rows[0].cells
        header[0].text = "Деятельность"
        header[1].text = "Компетенции"
        header[2].text = "Практический опыт"
        header[3].text = "Умения"
        header[4].text = "Знания"
        for item in draft.activity_matrix:
            row = table.add_row().cells
            row[0].text = item.activity
            row[1].text = item.competencies
            row[2].text = item.practical_experience
            row[3].text = item.skills
            row[4].text = item.knowledge

    def _build_study_plan_table(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("2.3. Учебный план")
        table = document.add_table(rows=3, cols=9)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.rows[0].cells[0].text = "№"
        table.rows[0].cells[1].text = "Наименование дисциплин (модулей)"
        table.rows[0].cells[2].text = "Всего часов"
        table.rows[0].cells[3].text = "ДОТ всего"
        table.rows[0].cells[4].text = "Лекции"
        table.rows[0].cells[5].text = "Лаб."
        table.rows[0].cells[6].text = "Практика"
        table.rows[0].cells[7].text = "СРС"
        table.rows[0].cells[8].text = "Контроль"
        table.rows[1].cells[0].text = ""
        table.rows[2].cells[0].text = ""
        for item in draft.study_plan:
            row = table.add_row().cells
            row[0].text = item.number
            row[1].text = item.name
            row[2].text = str(item.total_hours)
            row[3].text = str(item.distance_total)
            row[4].text = str(item.lectures)
            row[5].text = str(item.labs)
            row[6].text = str(item.practice)
            row[7].text = str(item.srs)
            row[8].text = item.intermediate_attestation or item.current_control

    def _build_working_programs_section(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("2.4. Рабочие учебные программы дисциплин/модулей")
        for line in self._split_block(draft.working_programs_block):
            self._add_non_empty_paragraph(document, line)

    def _build_calendar_variants_section(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("2.5. Календарный учебный график")
        self._add_non_empty_paragraph(document, draft.general_characteristics.calendar_variants_intro_1)
        self._add_non_empty_paragraph(document, draft.general_characteristics.calendar_variants_intro_2)

        for variant in draft.calendar_variants:
            self._add_non_empty_paragraph(document, variant.title)
            self._add_non_empty_paragraph(document, variant.description)
            table = document.add_table(rows=1, cols=5)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            header = table.rows[0].cells
            header[0].text = "Период"
            header[1].text = "Содержание"
            header[2].text = "Часы"
            header[3].text = "С ДОТ/преп."
            header[4].text = "СРС"
            for row_data in variant.rows:
                row = table.add_row().cells
                row[0].text = row_data.period
                row[1].text = row_data.content
                row[2].text = str(row_data.total_hours)
                row[3].text = str(row_data.distance_with_teacher)
                row[4].text = str(row_data.srs)

    def _build_organizational_section(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("3.1. Организационно-педагогические условия")
        for line in self._split_block(draft.organizational_conditions_block):
            self._add_non_empty_paragraph(document, line)

    def _build_assessment_section(self, document: Document, draft: CourseDraft) -> None:
        block = draft.assessment_block
        section_map = [
            ("3.2. Текущий контроль", block.current_control_block),
            ("3.3. Промежуточная аттестация", block.intermediate_attestation_block),
            ("3.3.1. Итоговая аттестация", block.final_attestation_intro_block),
            ("3.3.2. Форма и цели итоговой аттестации", block.final_attestation_form_and_goals_block),
            ("3.3.3. Требования к портфолио", block.portfolio_requirements_block),
            ("3.3.4. Порядок проведения", block.attestation_procedure_block),
            ("3.3.5. Структура доклада", block.report_structure_block),
            ("3.3.6. Результаты и пересдача", block.results_and_retake_block),
            ("3.4. Критерии оценки итогового экзамена", block.exam_grading_criteria_block),
        ]
        for title, content in section_map:
            document.add_paragraph(title)
            for line in self._split_block(content):
                self._add_non_empty_paragraph(document, line)
        document.add_paragraph("Примерные вопросы комиссии")
        for line in self._split_block(block.commission_questions_block):
            self._add_non_empty_paragraph(document, line)

    def _build_resources_tables(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("4. Материально-техническое обеспечение")
        facilities = document.add_table(rows=1, cols=3)
        facilities.rows[0].cells[0].text = "Наименование"
        facilities.rows[0].cells[1].text = "Вид занятий"
        facilities.rows[0].cells[2].text = "Оснащение"
        for item in draft.facilities:
            row = facilities.add_row().cells
            row[0].text = item.name
            row[1].text = item.lesson_type
            row[2].text = item.equipment

        document.add_paragraph("5. Цифровые ресурсы")
        resources = document.add_table(rows=1, cols=3)
        resources.rows[0].cells[0].text = "Наименование"
        resources.rows[0].cells[1].text = "Вид занятий"
        resources.rows[0].cells[2].text = "Оснащение"
        for item in draft.digital_resources:
            row = resources.add_row().cells
            row[0].text = item.name
            row[1].text = item.lesson_type
            row[2].text = item.equipment

    def _build_signatures(self, document: Document, draft: CourseDraft) -> None:
        document.add_paragraph("Подписи")
        table = document.add_table(rows=3, cols=2)
        table.rows[0].cells[0].text = draft.document_meta.approval_position
        table.rows[0].cells[1].text = draft.document_meta.approval_name
        table.rows[1].cells[0].text = draft.signatures.teacher_position
        table.rows[1].cells[1].text = draft.signatures.teacher_name
        table.rows[2].cells[0].text = draft.signatures.program_manager_position
        table.rows[2].cells[1].text = draft.signatures.program_manager_name

    def _add_non_empty_paragraph(self, document: Document, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        paragraph = document.add_paragraph(clean)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def _split_block(self, raw: str) -> list[str]:
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _slugify(self, text: str) -> str:
        normalized = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "program"

    def _set_paragraph_text(self, document: Document, original_text: str, new_text: str) -> None:
        for paragraph in document.paragraphs:
            if paragraph.text.strip() == original_text:
                paragraph.text = new_text
                return
        raise ValueError(f"Не найден абзац шаблона: {original_text}")

    def _replace_block_between_texts(
        self,
        document: Document,
        start_text: str,
        end_text: str,
        lines: list[str],
    ) -> None:
        start_index = self._find_paragraph_index(document, start_text)
        end_index = self._find_paragraph_index(document, end_text)
        if end_index <= start_index:
            raise ValueError(f"Некорректные границы блока: {start_text} -> {end_text}")

        existing = document.paragraphs[start_index + 1 : end_index]
        template_paragraph = next((paragraph for paragraph in existing if paragraph.text.strip()), document.paragraphs[start_index])
        for paragraph in existing:
            self._remove_paragraph(paragraph)

        end_paragraph = self._find_paragraph(document, end_text)
        for line in reversed(lines):
            if not line.strip():
                continue
            inserted = end_paragraph.insert_paragraph_before(line, style=template_paragraph.style)
            inserted.alignment = template_paragraph.alignment
            self._copy_run_format(template_paragraph, inserted)

    def _fill_labor_functions_table(self, table: Table, draft: CourseDraft) -> None:
        self._trim_table_rows(table, 1)
        for item in draft.labor_functions:
            row = table.add_row().cells
            row[0].text = item.name
            row[1].text = item.code_level
            row[2].text = "\n".join(item.competencies)

    def _fill_activity_matrix_table(self, table: Table, draft: CourseDraft) -> None:
        self._trim_table_rows(table, 1)
        for item in draft.activity_matrix:
            row = table.add_row().cells
            row[0].text = item.activity
            row[1].text = item.competencies
            row[2].text = item.practical_experience
            row[3].text = item.skills
            row[4].text = item.knowledge

    def _fill_study_plan_table(self, table: Table, draft: CourseDraft) -> None:
        self._trim_table_rows(table, 3)
        for item in draft.study_plan:
            row = table.add_row().cells
            values = [
                item.number,
                item.name,
                str(item.total_hours),
                str(item.distance_total),
                str(item.lectures),
                str(item.labs),
                str(item.practice),
                str(item.srs),
                item.current_control,
                item.intermediate_attestation,
            ]
            for index, value in enumerate(values):
                row[index].text = value

    def _fill_calendar_variant_table(self, table: Table, variant, draft: CourseDraft) -> None:
        self._trim_table_rows(table, 1)
        for row_data in variant.rows:
            row = table.add_row().cells
            row[0].text = row_data.period
            row[1].text = row_data.content
            row[2].text = str(row_data.total_hours)
            row[3].text = str(row_data.distance_with_teacher)
            row[4].text = str(row_data.srs)
            row[5].text = str(row_data.attestation)
            row[6].text = str(row_data.duration_weeks)
            row[7].text = str(row_data.hours_per_week)
            row[8].text = str(row_data.teacher_hours_per_week)

        total_row = table.add_row().cells
        total_row[0].text = "Итого:"
        total_row[2].text = str(draft.program_card.hours)
        total_row[3].text = str(sum(row.distance_with_teacher for row in variant.rows))
        total_row[4].text = str(sum(row.srs for row in variant.rows))
        total_row[5].text = str(sum(row.attestation for row in variant.rows))
        total_row[6].text = str(variant.total_weeks)

    def _fill_thematic_plan_table(self, table: Table, draft: CourseDraft) -> None:
        self._trim_table_rows(table, 3)
        module_rows = [row for row in draft.study_plan if row.number.isdigit()]
        module_total_hours = sum(row.total_hours for row in module_rows)
        module_total_distance = sum(row.distance_total for row in module_rows)
        module_total_lectures = sum(row.lectures for row in module_rows)
        module_total_labs = sum(row.labs for row in module_rows)
        module_total_practice = sum(row.practice for row in module_rows)
        module_total_srs = sum(row.srs for row in module_rows)

        summary_row = table.add_row().cells
        summary_values = ["№ модуля", "Наименование разделов, дисциплин, тем", str(module_total_hours), str(module_total_distance), str(module_total_lectures), str(module_total_labs), str(module_total_practice), str(module_total_srs), "", ""]
        for index, value in enumerate(summary_values):
            summary_row[index].text = value

        for module, plan_row in zip(draft.modules, module_rows):
            module_row = table.add_row().cells
            module_values = [
                f"Модуль {module.number}",
                module.name,
                str(plan_row.total_hours),
                str(plan_row.distance_total),
                str(plan_row.lectures),
                str(plan_row.labs),
                str(plan_row.practice),
                str(plan_row.srs),
                "",
                plan_row.intermediate_attestation or "Зачёт",
            ]
            for index, value in enumerate(module_values):
                module_row[index].text = value

            for theme_number, (theme_title, theme_hours) in enumerate(self._theme_rows(module), start=1):
                distance = min(theme_hours, max(0, int(round(theme_hours * 0.67))))
                lectures = distance // 2
                practice = distance - lectures
                srs = theme_hours - distance
                row = table.add_row().cells
                values = [
                    f"{module.number}.{theme_number}.",
                    theme_title,
                    str(theme_hours),
                    str(distance),
                    str(lectures),
                    "0",
                    str(practice),
                    str(srs),
                    "Выполнение практического задания",
                    "",
                ]
                for index, value in enumerate(values):
                    row[index].text = value

        prep = draft.study_plan[-3]
        exam = draft.study_plan[-2]
        total = draft.study_plan[-1]

        prep_row = table.add_row().cells
        prep_values = ["", prep.name, str(prep.total_hours), str(prep.distance_total), str(prep.lectures), str(prep.labs), str(prep.practice), str(prep.srs), "", ""]
        for index, value in enumerate(prep_values):
            prep_row[index].text = value

        exam_row = table.add_row().cells
        exam_values = ["", exam.name, str(exam.total_hours), str(exam.distance_total), str(exam.lectures), str(exam.labs), str(exam.practice), str(exam.srs), "", exam.intermediate_attestation]
        for index, value in enumerate(exam_values):
            exam_row[index].text = value

        total_row = table.add_row().cells
        total_values = ["", total.name, str(total.total_hours), str(total.distance_total), str(total.lectures), str(total.labs), str(total.practice), str(total.srs), "", total.intermediate_attestation]
        for index, value in enumerate(total_values):
            total_row[index].text = value

    def _fill_resources_table(self, table: Table, items) -> None:
        self._trim_table_rows(table, 1)
        for item in items:
            row = table.add_row().cells
            row[0].text = item.name
            row[1].text = item.lesson_type
            row[2].text = item.equipment

    def _trim_table_rows(self, table: Table, keep_rows: int) -> None:
        while len(table.rows) > keep_rows:
            self._remove_table_row(table.rows[-1])

    def _theme_rows(self, module) -> list[tuple[str, int]]:
        themes = module.theme_titles or [module.name]
        raw = [module.hours / len(themes)] * len(themes)
        floors = [max(1, int(value)) for value in raw]
        remainder = module.hours - sum(floors)
        order = list(range(len(themes)))
        index = 0
        while remainder > 0 and order:
            floors[order[index % len(order)]] += 1
            remainder -= 1
            index += 1
        return list(zip(themes, floors))

    def _find_paragraph_index(self, document: Document, text: str) -> int:
        for index, paragraph in enumerate(document.paragraphs):
            if paragraph.text.strip() == text:
                return index
        raise ValueError(f"Не найден абзац шаблона: {text}")

    def _find_paragraph(self, document: Document, text: str) -> Paragraph:
        return document.paragraphs[self._find_paragraph_index(document, text)]

    def _remove_paragraph(self, paragraph: Paragraph) -> None:
        paragraph._element.getparent().remove(paragraph._element)

    def _remove_table_row(self, row) -> None:
        row._tr.getparent().remove(row._tr)

    def _copy_run_format(self, template: Paragraph, target: Paragraph) -> None:
        if not template.runs or not target.runs:
            return
        source_run = template.runs[0]
        target_run = target.runs[0]
        target_run.bold = source_run.bold
        target_run.italic = source_run.italic
        target_run.underline = source_run.underline
        target_run.font.name = source_run.font.name
        target_run.font.size = source_run.font.size

    def _format_organization_name(self, organization_name: str) -> str:
        normalized = organization_name.strip()
        if normalized.upper().startswith("ООО"):
            suffix = normalized[3:].strip()
            if suffix:
                return f"ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕСТВЕННОСТЬЮ {suffix}".upper()
        return normalized.upper()

    def _ensure_period(self, text: str, use_semicolon: bool = False, is_last: bool = True) -> str:
        cleaned = text.strip().rstrip(".;")
        if use_semicolon and not is_last:
            return cleaned + ";"
        return cleaned + "."

    def _academic_hours_total_phrase(self, value: int) -> str:
        ending = self._plural(value, "академический час", "академических часа", "академических часов")
        return f"{value} {ending}."

    def _plural(self, value: int, one: str, few: str, many: str) -> str:
        remainder_100 = value % 100
        remainder_10 = value % 10
        if 11 <= remainder_100 <= 14:
            return many
        if remainder_10 == 1:
            return one
        if 2 <= remainder_10 <= 4:
            return few
        return many
