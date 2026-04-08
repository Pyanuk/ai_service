from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, HttpUrl


class ModuleSeed(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    desired_hours: int = Field(gt=0)
    summary: str = Field(min_length=1)


class Constraints(BaseModel):
    standards: List[str] = Field(default_factory=list)
    required_phrases: List[str] = Field(default_factory=list)
    city: str = Field(default="Москва")
    document_year: int = Field(default=2026, ge=2024, le=2100)
    organization_name: str = Field(min_length=1)
    approval_position: str = Field(default="Генеральный директор")
    approval_name: str = Field(min_length=1)
    approval_date: str = Field(default="«___» ____________ {{year}} г.")
    teacher_name: str = Field(default="________________")
    teacher_position: str = Field(default="Преподаватель")
    program_manager_name: str = Field(default="________________")
    program_manager_position: str = Field(
        default="Руководитель направления дополнительного профессионального образования"
    )


class PricingMeta(BaseModel):
    price: str = Field(min_length=1)
    lessons_count: int = Field(ge=0)
    program_view: str = Field(min_length=1, max_length=50)


class CourseSeedRequest(BaseModel):
    course_name: str = Field(min_length=1, max_length=500)
    program_type: str = Field(min_length=1, max_length=255)
    format: str = Field(min_length=1, max_length=255)
    hours: int = Field(gt=0)
    target_audience: str = Field(min_length=1)
    qualification: str = Field(min_length=1)
    professional_area: str = Field(min_length=1)
    training_goal: str = Field(min_length=1)
    brief_description: str = Field(min_length=1)
    modules_seed: List[ModuleSeed] = Field(min_length=1)
    constraints: Constraints
    pricing_meta: PricingMeta
    source_url: HttpUrl | None = None
