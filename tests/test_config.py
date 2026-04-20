from __future__ import annotations

from app.config import Settings


def test_settings_use_qwen_2_5_7b_by_default(monkeypatch):
    monkeypatch.delenv("AI_SERVICE_OLLAMA_MODEL", raising=False)

    settings = Settings.from_env()

    assert settings.ollama_model == "qwen2.5:7b"


def test_settings_allow_ollama_model_override(monkeypatch):
    monkeypatch.setenv("AI_SERVICE_OLLAMA_MODEL", "custom-model")

    settings = Settings.from_env()

    assert settings.ollama_model == "custom-model"
