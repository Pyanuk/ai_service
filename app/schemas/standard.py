from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, HttpUrl


class StandardResolveRequest(BaseModel):
    fgos_url: HttpUrl | None = None
    fgos_code: str | None = Field(default=None, min_length=1, max_length=50)
    course_name: str | None = Field(default=None, max_length=500)
    professional_area: str | None = Field(default=None)
    training_goal: str | None = Field(default=None)
    brief_description: str | None = Field(default=None)
    module_names: List[str] = Field(default_factory=list)
    module_summaries: List[str] = Field(default_factory=list)
    standard_track_id: str | None = Field(default=None, max_length=100)


class StandardTrackOption(BaseModel):
    track_id: str
    qualification_title: str


class StandardResolveResponse(BaseModel):
    supported: bool
    detail: str
    fgos_code: str
    standard_profile_id: str | None = None
    fgos_title: str | None = None
    order_title: str | None = None
    source_url: HttpUrl | None = None
    professional_area: str | None = None
    resolved_track_id: str | None = None
    qualification_title: str | None = None
    supported_tracks: List[StandardTrackOption] = Field(default_factory=list)
    detected_competencies: List[str] = Field(default_factory=list)
