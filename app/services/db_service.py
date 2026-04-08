from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover - dependency may be absent in some local envs
    psycopg2 = None

from app.config import Settings
from app.schemas.draft import CourseDraft
from app.services.errors import DatabaseUnavailableError


@dataclass
class ConfirmedDraftResult:
    program_id: int
    document_path: str


class DbService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def check_health(self) -> bool:
        if psycopg2 is None:
            return False
        try:
            with psycopg2.connect(self._settings.database_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception:
            return False

    def save_confirmed_draft(self, draft: CourseDraft, document_path: Path) -> ConfirmedDraftResult:
        if psycopg2 is None:
            raise DatabaseUnavailableError("psycopg2 не установлен.")
        try:
            with psycopg2.connect(self._settings.database_dsn) as conn:
                with conn.cursor() as cur:
                    self._ensure_profile_table(cur)
                    cur.execute(
                        """
                        INSERT INTO learning_program
                            (name, format, program_view_id, hours, lessons_count, price, image, source_url)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            draft.program_card.course_name,
                            draft.program_card.format,
                            self._program_view_id(draft.program_card.program_view, draft.program_card.program_type),
                            draft.program_card.hours,
                            draft.program_card.lessons_count,
                            self._parse_price(draft.program_card.price),
                            None,
                            draft.program_card.source_url,
                        ),
                    )
                    program_id = int(cur.fetchone()[0])

                    for module in draft.modules:
                        cur.execute(
                            """
                            INSERT INTO program_module
                                (program_id, module_number, module_name, description, hours)
                            VALUES
                                (%s, %s, %s, %s, %s)
                            """,
                            (
                                program_id,
                                module.number,
                                module.name,
                                module.description,
                                module.hours,
                            ),
                        )

                    cur.execute(
                        """
                        INSERT INTO program_profile_json
                            (program_id, version, profile_json, template_name, generated_docx_path)
                        VALUES
                            (%s, %s, %s::jsonb, %s, %s)
                        """,
                        (
                            program_id,
                            draft.document_meta.version,
                            json.dumps(draft.model_dump(mode="json"), ensure_ascii=False),
                            draft.document_meta.template_name,
                            str(document_path),
                        ),
                    )
                conn.commit()
        except Exception as exc:
            raise DatabaseUnavailableError(f"Не удалось сохранить данные в БД: {exc}") from exc

        return ConfirmedDraftResult(program_id=program_id, document_path=str(document_path))

    def _ensure_profile_table(self, cur) -> None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS program_profile_json (
                id BIGSERIAL PRIMARY KEY,
                program_id BIGINT NOT NULL REFERENCES learning_program(id) ON DELETE CASCADE,
                version INTEGER NOT NULL DEFAULT 1,
                profile_json JSONB NOT NULL,
                template_name VARCHAR(255),
                generated_docx_path VARCHAR(1000),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )

    def _program_view_id(self, program_view: str, program_type: str) -> int:
        normalized_view = program_view.strip().upper()
        if normalized_view == "ДОП":
            return 1
        if normalized_view == "ПП":
            return 2
        if normalized_view == "ПК":
            return 3
        normalized_type = program_type.lower()
        if "доп" in normalized_type:
            return 1
        if "повыш" in normalized_type or "квалификац" in normalized_type:
            return 3
        return 2

    def _parse_price(self, raw: str) -> float:
        return float(raw.replace(" ", "").replace(",", "."))
