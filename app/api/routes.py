from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.schemas.course import CourseSeedRequest
from app.schemas.draft import (
    ConfirmDraftResponse,
    DocumentExportResponse,
    GenerateDraftResponse,
    HealthResponse,
    UpdateDraftRequest,
)
from app.schemas.standard import StandardResolveRequest, StandardResolveResponse
from app.services.errors import (
    DatabaseUnavailableError,
    DraftNotFoundError,
    DraftValidationError,
    OllamaUnavailableError,
)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["ai-service"])

    @router.get("/health", response_model=HealthResponse)
    def health(services=Depends(_get_services)) -> HealthResponse:
        settings = services["settings"]
        return HealthResponse(
            service="ai_service",
            template_exists=services["document_builder"].template_available(),
            ollama_available=services["ollama_service"].check_health(),
            db_available=services["db_service"].check_health(),
        )

    @router.post("/standards/resolve", response_model=StandardResolveResponse)
    def resolve_standard(payload: StandardResolveRequest, services=Depends(_get_services)) -> StandardResolveResponse:
        try:
            return services["standards_service"].resolve(payload)
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/standards/resolve-pdf", response_model=StandardResolveResponse)
    async def resolve_standard_pdf(
        fgos_pdf: UploadFile = File(...),
        fgos_code: str = Form(...),
        services=Depends(_get_services),
    ) -> StandardResolveResponse:
        try:
            payload = StandardResolveRequest(fgos_code=fgos_code)
            return services["standards_service"].resolve_pdf(
                filename=fgos_pdf.filename or "standard.pdf",
                pdf_bytes=await fgos_pdf.read(),
                payload=payload,
            )
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/course-drafts/generate", response_model=GenerateDraftResponse)
    def generate_course_draft(payload: CourseSeedRequest, services=Depends(_get_services)) -> GenerateDraftResponse:
        try:
            draft = services["draft_builder"].build_draft(payload)
            services["draft_storage"].save_draft(draft)
            return GenerateDraftResponse(draft_id=draft.draft_id, draft=draft)
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OllamaUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - guard rail
            raise HTTPException(status_code=500, detail=f"{exc.__class__.__name__}: {exc}") from exc

    @router.get("/course-drafts/{draft_id}", response_model=GenerateDraftResponse)
    def get_draft(draft_id: str, services=Depends(_get_services)) -> GenerateDraftResponse:
        try:
            draft = services["draft_storage"].load_draft(draft_id)
            return GenerateDraftResponse(draft_id=draft_id, draft=draft)
        except DraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/course-drafts/{draft_id}", response_model=GenerateDraftResponse)
    def update_draft(draft_id: str, payload: UpdateDraftRequest, services=Depends(_get_services)) -> GenerateDraftResponse:
        try:
            draft = services["draft_storage"].update_draft(draft_id, payload.updates)
            return GenerateDraftResponse(draft_id=draft_id, draft=draft)
        except DraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/course-drafts/{draft_id}/export-docx", response_model=DocumentExportResponse)
    def export_docx(draft_id: str, services=Depends(_get_services)) -> DocumentExportResponse:
        try:
            draft = services["draft_storage"].load_draft(draft_id)
            path = services["document_builder"].build_document(draft)
            return DocumentExportResponse(draft_id=draft_id, document_path=str(path))
        except DraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - guard rail
            raise HTTPException(status_code=500, detail=f"{exc.__class__.__name__}: {exc}") from exc

    @router.post("/course-drafts/{draft_id}/confirm", response_model=ConfirmDraftResponse)
    def confirm_draft(draft_id: str, services=Depends(_get_services)) -> ConfirmDraftResponse:
        try:
            draft = services["draft_storage"].load_draft(draft_id)
            path = services["document_builder"].build_document(draft)
            result = services["db_service"].save_confirmed_draft(draft, path)
            return ConfirmDraftResponse(program_id=result.program_id, document_path=result.document_path)
        except DraftNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DatabaseUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - guard rail
            raise HTTPException(status_code=500, detail=f"{exc.__class__.__name__}: {exc}") from exc

    return router


def _get_services(request: Request):
    return request.app.state.services
