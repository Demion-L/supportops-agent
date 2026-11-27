"""Типизированные ошибки Agent Commons.

Идея заимствована из MONOLITH Durable State (стабильные коды ошибок; вызывающий
код ветвится по `code`, а не по тексту). Сообщения не содержат полезной нагрузки.
"""
from __future__ import annotations


class CommonsError(Exception):
    """Базовая ошибка; `code` стабилен и пригоден для ветвления."""

    code = "COMMONS"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class ValidationError(CommonsError):
    code = "VALIDATION"


class TransitionError(CommonsError):
    code = "TRANSITION"


class UnknownEntityError(CommonsError):
    code = "UNKNOWN"


class DuplicateError(CommonsError):
    code = "DUPLICATE"


class ConflictError(CommonsError):
    code = "CONFLICT"


class ForbiddenError(CommonsError):
    code = "FORBIDDEN"


class LogCorruptError(CommonsError):
    code = "LOG_CORRUPT"


class CommonsUnavailable(CommonsError):
    """Commons недоступен (отказ, таймаут). Агент A обязан продолжить локально (S6)."""

    code = "UNAVAILABLE"
