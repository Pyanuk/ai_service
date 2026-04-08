from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.schemas.draft import CourseDraft
from app.services.errors import DraftNotFoundError, DraftValidationError
from app.services.validation_service import ValidationService


class DraftStorageService:
    def __init__(self, settings: Settings, validation_service: ValidationService) -> None:
        self._settings = settings
        self._validation = validation_service

    def save_draft(self, draft: CourseDraft) -> Path:
        path = self._path_for(draft.draft_id)
        path.write_text(
            json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_draft(self, draft_id: str) -> CourseDraft:
        path = self._path_for(draft_id)
        if not path.exists():
            raise DraftNotFoundError(f"Черновик {draft_id} не найден.")
        return CourseDraft.model_validate_json(path.read_text(encoding="utf-8"))

    def update_draft(self, draft_id: str, updates: dict[str, Any]) -> CourseDraft:
        if not isinstance(updates, dict):
            raise DraftValidationError("Обновления черновика должны быть объектом JSON.")

        current = self.load_draft(draft_id)
        current_data = current.model_dump(mode="json")

        for key, value in updates.items():
            if key not in current_data:
                continue
            if isinstance(current_data[key], dict) and isinstance(value, dict):
                current_data[key] = self._deep_merge(current_data[key], value)
            else:
                current_data[key] = value

        current_data["draft_id"] = draft_id
        current_data["document_meta"]["updated_at"] = datetime.utcnow().isoformat()
        updated = CourseDraft.model_validate(current_data)
        self._validation.validate_draft(updated)
        self.save_draft(updated)
        return updated

    def _path_for(self, draft_id: str) -> Path:
        return self._settings.drafts_dir / f"{draft_id}.json"

    def _deep_merge(self, target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        result = dict(target)
        for key, value in source.items():
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
