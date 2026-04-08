class DraftValidationError(Exception):
    """Raised when the generated draft is invalid."""


class DraftNotFoundError(FileNotFoundError):
    """Raised when a draft cannot be found in storage."""


class OllamaUnavailableError(RuntimeError):
    """Raised when Ollama cannot answer a request."""


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL is unavailable."""
