from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    service_root: Path
    template_path: Path
    drafts_dir: Path
    output_dir: Path
    ollama_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    preparation_hours: int
    final_attestation_hours: int

    @property
    def database_dsn(self) -> str:
        return (
            f"host={self.db_host} "
            f"port={self.db_port} "
            f"dbname={self.db_name} "
            f"user={self.db_user} "
            f"password={self.db_password}"
        )

    @classmethod
    def from_env(cls) -> "Settings":
        service_root = Path(__file__).resolve().parents[1]
        project_root = service_root.parent

        load_dotenv(project_root / ".env", override=False)
        load_dotenv(service_root / ".env", override=False)

        default_template_path = service_root / "program_template.docx"
        preferred_examples = [
            service_root / "правильный пример.docx",
            service_root / "reference_example.docx",
            service_root / "program_template.docx",
        ]
        for candidate in preferred_examples:
            if candidate.exists():
                default_template_path = candidate
                break

        template_path = Path(
            os.getenv("AI_SERVICE_TEMPLATE_PATH", str(default_template_path))
        )

        return cls(
            project_root=project_root,
            service_root=service_root,
            template_path=template_path,
            drafts_dir=Path(os.getenv("AI_SERVICE_DRAFTS_DIR", str(service_root / "storage" / "drafts"))),
            output_dir=Path(os.getenv("AI_SERVICE_OUTPUT_DIR", str(service_root / "storage" / "output"))),
            ollama_url=os.getenv("AI_SERVICE_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("AI_SERVICE_OLLAMA_MODEL", "qwen2.5:7b"),
            ollama_timeout_seconds=int(os.getenv("AI_SERVICE_OLLAMA_TIMEOUT", "30")),
            db_host=os.getenv("DB_HOST", "127.0.0.1"),
            db_port=int(os.getenv("DB_PORT", "5432")),
            db_name=os.getenv("DB_NAME", "postgres"),
            db_user=os.getenv("DB_USER", "postgres"),
            db_password=os.getenv("DB_PASSWORD", ""),
            preparation_hours=int(os.getenv("AI_SERVICE_PREPARATION_HOURS", "8")),
            final_attestation_hours=int(os.getenv("AI_SERVICE_FINAL_ATTESTATION_HOURS", "8")),
        )

    def ensure_directories(self) -> None:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
