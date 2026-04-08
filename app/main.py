from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import create_router
from app.config import Settings
from app.services.db_service import DbService
from app.services.document_builder import DocumentBuilder
from app.services.draft_builder import DraftBuilder
from app.services.draft_storage import DraftStorageService
from app.services.ollama_service import OllamaService
from app.services.validation_service import ValidationService


def create_app(service_overrides: dict | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()

    validation_service = ValidationService(settings)
    ollama_service = OllamaService(settings)

    services = {
        "settings": settings,
        "validation_service": validation_service,
        "ollama_service": ollama_service,
        "draft_storage": DraftStorageService(settings, validation_service),
        "document_builder": DocumentBuilder(settings),
        "db_service": DbService(settings),
    }
    services["draft_builder"] = DraftBuilder(settings, ollama_service, validation_service)

    if service_overrides:
        services.update(service_overrides)

    api = FastAPI(title="AI Course Builder", version="1.0.0")
    api.include_router(create_router())
    api.state.services = services
    return api


app = create_app()
