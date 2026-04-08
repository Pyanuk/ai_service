from __future__ import annotations

from datetime import datetime
from typing import Any, List

from pydantic import BaseModel, Field

from app.schemas.course import CourseSeedRequest


class ProgramCard(BaseModel):
    course_name: str
    course_name_upper: str
    program_type: str
    format: str
    hours: int
    target_audience: str
    qualification: str
    professional_area: str
    training_goal: str
    brief_description: str
    price: str
    lessons_count: int
    program_view: str
    source_url: str | None = None


class GeneralCharacteristics(BaseModel):
    program_goal: str
    professional_area: str
    professional_objects: List[str]
    activity_types: List[str]
    qualification_level: str
    audience_requirements: List[str]
    additional_requirements: List[str]
    entry_requirements: str
    education_form: str
    final_attestation_result: str
    parallel_education_note: str
    standards_basis: str
    calendar_variants_intro_1: str
    calendar_variants_intro_2: str


class LaborFunctionEntry(BaseModel):
    name: str
    code_level: str
    competencies: List[str]


class ActivityMatrixEntry(BaseModel):
    activity: str
    competencies: str
    practical_experience: str
    skills: str
    knowledge: str


class ModuleDraft(BaseModel):
    number: int
    name: str
    hours: int
    summary: str
    description: str
    theme_titles: List[str] = Field(default_factory=list)


class StudyPlanEntry(BaseModel):
    number: str
    name: str
    total_hours: int
    distance_total: int
    lectures: int
    labs: int
    practice: int
    srs: int
    current_control: str = ""
    intermediate_attestation: str = ""


class CalendarVariantRow(BaseModel):
    period: str
    content: str
    total_hours: int
    distance_with_teacher: int
    srs: int
    attestation: int
    duration_weeks: int
    hours_per_week: int
    teacher_hours_per_week: int


class CalendarVariant(BaseModel):
    title: str
    description: str
    rows: List[CalendarVariantRow]
    total_weeks: int


class AssessmentBlock(BaseModel):
    current_control_block: str
    intermediate_attestation_block: str
    final_attestation_intro_block: str
    final_attestation_form_and_goals_block: str
    portfolio_requirements_block: str
    attestation_procedure_block: str
    report_structure_block: str
    commission_questions_block: str
    results_and_retake_block: str
    exam_grading_criteria_block: str


class Signatures(BaseModel):
    approval_signature_line: str
    teacher_signature_line: str
    teacher_name: str
    teacher_position: str
    program_manager_signature_line: str
    program_manager_name: str
    program_manager_position: str


class DocumentMeta(BaseModel):
    organization_name: str
    approval_position: str
    approval_name: str
    approval_date: str
    city: str
    document_year: int
    template_name: str
    created_at: datetime
    updated_at: datetime
    version: int = 1


class FacilityEntry(BaseModel):
    name: str
    lesson_type: str
    equipment: str


class DigitalResourceEntry(BaseModel):
    name: str
    lesson_type: str
    equipment: str


class CourseDraft(BaseModel):
    draft_id: str
    status: str = "draft"
    seed: CourseSeedRequest
    program_card: ProgramCard
    general_characteristics: GeneralCharacteristics
    labor_functions: List[LaborFunctionEntry]
    activity_matrix: List[ActivityMatrixEntry]
    modules: List[ModuleDraft]
    study_plan: List[StudyPlanEntry]
    calendar_variants: List[CalendarVariant]
    working_programs_block: str
    organizational_conditions_block: str
    assessment_block: AssessmentBlock
    signatures: Signatures
    document_meta: DocumentMeta
    facilities: List[FacilityEntry]
    digital_resources: List[DigitalResourceEntry]


class GenerateDraftResponse(BaseModel):
    draft_id: str
    draft: CourseDraft


class UpdateDraftRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class DocumentExportResponse(BaseModel):
    draft_id: str
    document_path: str


class ConfirmDraftResponse(BaseModel):
    program_id: int
    document_path: str


class HealthResponse(BaseModel):
    service: str
    template_exists: bool
    ollama_available: bool
    db_available: bool
