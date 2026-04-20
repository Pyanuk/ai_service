from __future__ import annotations

from app.config import Settings
from app.schemas.course import CourseSeedRequest
from app.schemas.draft import CourseDraft
from app.services.errors import DraftValidationError
from app.services.standard_profiles import resolve_standard_profile


class ValidationService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_seed(self, seed: CourseSeedRequest) -> None:
        resolve_standard_profile(seed)
        if not seed.modules_seed:
            raise DraftValidationError("Необходимо указать хотя бы один модуль.")
        if seed.hours <= 0:
            raise DraftValidationError("Количество часов должно быть больше нуля.")
        if sum(module.desired_hours for module in seed.modules_seed) <= 0:
            raise DraftValidationError("Нельзя распределить часы по модулям.")
        if not seed.course_name.strip():
            raise DraftValidationError("Название курса не может быть пустым.")
        if not seed.pricing_meta.program_view.strip():
            raise DraftValidationError("Не указан вид программы.")

    def validate_draft(self, draft: CourseDraft) -> None:
        self.validate_seed(draft.seed)
        if not draft.modules:
            raise DraftValidationError("Черновик не содержит модулей.")
        if not draft.study_plan:
            raise DraftValidationError("Черновик не содержит учебный план.")
        if sum(module.hours for module in draft.modules) <= 0:
            raise DraftValidationError("Сумма часов по модулям должна быть больше нуля.")
        if len(draft.calendar_variants) != 5:
            raise DraftValidationError("Для документа должно быть подготовлено 5 вариантов нагрузки.")
        total_row = draft.study_plan[-1]
        if total_row.total_hours != draft.program_card.hours:
            raise DraftValidationError("Итоговая строка учебного плана не совпадает с объёмом программы.")
