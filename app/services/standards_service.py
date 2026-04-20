from __future__ import annotations

import re
import zlib
from io import BytesIO

from app.schemas.standard import StandardResolveRequest, StandardResolveResponse, StandardTrackOption
from app.services.errors import DraftValidationError
from app.services.standard_profiles import (
    FGOS_CODE_TO_PROFILE_ID,
    detect_track_from_text,
    extract_dynamic_competencies_from_text,
    extract_fgos_code,
    get_standard_profile_by_reference,
    register_dynamic_profile,
)


class StandardsService:
    def resolve(self, payload: StandardResolveRequest) -> StandardResolveResponse:
        normalized_code = extract_fgos_code(payload.fgos_code) or extract_fgos_code(
            str(payload.fgos_url) if payload.fgos_url else None
        )
        if not normalized_code:
            raise DraftValidationError("Не удалось определить код ФГОС по переданным данным.")

        return self._resolve_with_context(
            normalized_code=normalized_code,
            fgos_url=str(payload.fgos_url) if payload.fgos_url else None,
            source_reference=str(payload.fgos_url) if payload.fgos_url else None,
            course_name=payload.course_name or "",
            professional_area=payload.professional_area or "",
            training_goal=payload.training_goal or "",
            brief_description=payload.brief_description or "",
            module_names=tuple(payload.module_names),
            module_summaries=tuple(payload.module_summaries),
            standard_track_id=payload.standard_track_id,
        )

    def resolve_pdf(
        self,
        filename: str,
        pdf_bytes: bytes,
        payload: StandardResolveRequest,
    ) -> StandardResolveResponse:
        if not pdf_bytes:
            raise DraftValidationError("PDF-файл пустой.")

        extracted_text = self.extract_text_from_pdf_bytes(pdf_bytes)
        normalized_code = (
            extract_fgos_code(payload.fgos_code)
            or extract_fgos_code(extracted_text)
            or extract_fgos_code(filename)
        )
        if not normalized_code:
            raise DraftValidationError(
                "Не удалось определить код ФГОС из PDF. Передай fgos_code вместе с файлом или используй PDF с распознаваемым текстом."
            )

        return self._resolve_with_context(
            normalized_code=normalized_code,
            fgos_url=None,
            source_reference=f"https://registry.local/fgos/{normalized_code}",
            course_name=payload.course_name or "",
            professional_area=payload.professional_area or "",
            training_goal=payload.training_goal or "",
            brief_description=payload.brief_description or "",
            module_names=tuple(payload.module_names),
            module_summaries=tuple(payload.module_summaries),
            standard_track_id=payload.standard_track_id,
            extracted_text=extracted_text,
        )

    def _resolve_with_context(
        self,
        *,
        normalized_code: str,
        fgos_url: str | None,
        source_reference: str | None,
        course_name: str,
        professional_area: str,
        training_goal: str,
        brief_description: str,
        module_names: tuple[str, ...],
        module_summaries: tuple[str, ...],
        standard_track_id: str | None,
        extracted_text: str = "",
    ) -> StandardResolveResponse:
        detected_competencies = list(extract_dynamic_competencies_from_text(extracted_text))
        profile = get_standard_profile_by_reference(fgos_code=normalized_code, fgos_url=fgos_url)
        auto_created = False
        should_refresh_dynamic_profile = normalized_code not in FGOS_CODE_TO_PROFILE_ID and bool(source_reference)
        if should_refresh_dynamic_profile:
            if extracted_text.strip() and not detected_competencies:
                raise DraftValidationError(
                    f"Не удалось извлечь профессиональные компетенции из PDF ФГОС {normalized_code}. "
                    "Сервис не будет регистрировать generic-профиль с неверными ПК; попробуй другой PDF или PDF с распознаваемым текстом."
                )
            profile = register_dynamic_profile(
                fgos_code=normalized_code,
                source_url=fgos_url or source_reference or f"https://registry.local/fgos/{normalized_code}",
                title=self._extract_fgos_title(extracted_text, normalized_code),
                order_title=self._extract_order_title(extracted_text),
                course_name=course_name,
                professional_area=professional_area,
                training_goal=training_goal,
                brief_description=brief_description,
                extracted_text=extracted_text,
            )
            auto_created = True
        elif profile is None:
            if source_reference:
                profile = register_dynamic_profile(
                    fgos_code=normalized_code,
                    source_url=fgos_url or source_reference,
                    title=self._extract_fgos_title(extracted_text, normalized_code),
                    order_title=self._extract_order_title(extracted_text),
                    course_name=course_name,
                    professional_area=professional_area,
                    training_goal=training_goal,
                    brief_description=brief_description,
                    extracted_text=extracted_text,
                )
                auto_created = True
            else:
                return StandardResolveResponse(
                    supported=False,
                    detail=(
                        f"ФГОС {normalized_code} распознан, но профиль пока не добавлен в реестр сервиса. "
                        "Передай ссылку или PDF, чтобы сервис автоматически зарегистрировал профиль."
                    ),
                    fgos_code=normalized_code,
                    detected_competencies=detected_competencies,
                )

        if standard_track_id:
            track = profile.get_track(standard_track_id)
        else:
            track = profile.get_track(
                detect_track_from_text(
                    profile,
                    course_name=course_name,
                    professional_area=professional_area,
                    training_goal=training_goal,
                    brief_description=brief_description,
                    module_names=module_names,
                    module_summaries=module_summaries,
                )
            )

        detail = "Профиль ФГОС распознан и готов к использованию при генерации документа."
        if auto_created:
            detail = (
                "Профиль ФГОС автоматически зарегистрирован в локальном реестре сервиса и готов к использованию "
                "при генерации документа."
            )

        return StandardResolveResponse(
            supported=True,
            detail=detail,
            fgos_code=profile.fgos_code,
            standard_profile_id=profile.profile_id,
            fgos_title=profile.title,
            order_title=profile.order_title,
            source_url=profile.source_url,
            professional_area=profile.professional_area,
            resolved_track_id=track.track_id,
            qualification_title=track.qualification_title,
            supported_tracks=[
                StandardTrackOption(track_id=item.track_id, qualification_title=item.qualification_title)
                for item in profile.tracks
            ],
            detected_competencies=detected_competencies,
        )

    def extract_text_from_pdf_bytes(self, pdf_bytes: bytes) -> str:
        pypdf_text = self._extract_text_with_pypdf(pdf_bytes)
        if pypdf_text.strip():
            return pypdf_text

        fallback_text = self._extract_text_with_fallback(pdf_bytes)
        if fallback_text.strip():
            return fallback_text

        raise DraftValidationError("Не удалось извлечь текст из PDF-файла ФГОС.")

    def _extract_text_with_pypdf(self, pdf_bytes: bytes) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            return ""

        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""

    def _extract_text_with_fallback(self, pdf_bytes: bytes) -> str:
        fragments: list[str] = []

        for encoding in ("utf-8", "latin-1", "cp1251"):
            try:
                fragments.append(pdf_bytes.decode(encoding, errors="ignore"))
            except Exception:
                continue

        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.DOTALL):
            stream = match.group(1).strip(b"\r\n")
            fragments.extend(self._decode_pdf_stream(stream))

        return "\n".join(fragment for fragment in fragments if fragment.strip())

    def _decode_pdf_stream(self, stream: bytes) -> list[str]:
        variants = [stream]
        try:
            variants.append(zlib.decompress(stream))
        except Exception:
            pass

        decoded: list[str] = []
        for variant in variants:
            for encoding in ("utf-8", "latin-1", "cp1251"):
                try:
                    text = variant.decode(encoding, errors="ignore")
                except Exception:
                    continue
                decoded.append(text)
                decoded.extend(re.findall(r"\(([^()]*)\)", text))
        return decoded

    def _extract_fgos_title(self, extracted_text: str, fgos_code: str) -> str | None:
        if not extracted_text.strip():
            return None
        pattern = re.search(
            rf"(ФГОС(?:\s+СПО)?\s+{re.escape(fgos_code)}[^\n\r]*)",
            extracted_text,
            flags=re.IGNORECASE,
        )
        if pattern:
            return pattern.group(1).strip()
        return f"ФГОС СПО {fgos_code}"

    def _extract_order_title(self, extracted_text: str) -> str | None:
        if not extracted_text.strip():
            return None
        pattern = re.search(
            r"(приказ[^\n\r]{0,160}№\s*[0-9А-Яа-я\-]+[^\n\r]{0,120})",
            extracted_text,
            flags=re.IGNORECASE,
        )
        if pattern:
            return pattern.group(1).strip()
        return None
