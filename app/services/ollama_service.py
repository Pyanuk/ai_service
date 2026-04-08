from __future__ import annotations

import json
from typing import Any

from urllib.error import URLError
from urllib.request import Request, urlopen

from app.config import Settings
from app.services.errors import OllamaUnavailableError


class OllamaService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def check_health(self) -> bool:
        try:
            request = Request(
                f"{self._settings.ollama_url.rstrip('/')}/api/tags",
                method="GET",
            )
            with urlopen(request, timeout=5) as response:
                response.read()
            return True
        except Exception:
            return False

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            request = Request(
                f"{self._settings.ollama_url.rstrip('/')}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self._settings.ollama_timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            raise OllamaUnavailableError("Не удалось получить ответ от Ollama.") from exc
        except Exception as exc:
            raise OllamaUnavailableError("Не удалось получить ответ от Ollama.") from exc

        text = str(data.get("response", "")).strip()
        if not text:
            raise OllamaUnavailableError("Ollama вернул пустой ответ.")
        return text

    def generate_json(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        raw = self.generate_text(
            "Верни только корректный JSON без пояснений и markdown.\n" + prompt,
            system_prompt=system_prompt,
        )
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise OllamaUnavailableError("Ollama не вернул корректный JSON.")
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OllamaUnavailableError("Не удалось разобрать JSON от Ollama.") from exc
