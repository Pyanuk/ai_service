from __future__ import annotations

import pytest

from app.config import Settings
from app.schemas.course import CourseSeedRequest
from app.services.errors import DraftValidationError
from app.services.validation_service import ValidationService


def test_validate_seed_rejects_empty_modules(seed_payload):
    broken = dict(seed_payload)
    broken["modules_seed"] = []
    with pytest.raises(Exception):
        CourseSeedRequest.model_validate(broken)


def test_validate_seed_rejects_zero_module_hours(seed_payload):
    service = ValidationService(Settings.from_env())
    broken = dict(seed_payload)
    broken["modules_seed"] = [
        {
            "name": "Пустой модуль",
            "desired_hours": 0,
            "summary": "Некорректный модуль",
        }
    ]
    with pytest.raises(Exception):
        CourseSeedRequest.model_validate(broken)
