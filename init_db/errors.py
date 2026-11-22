"""Понятные ошибки для пользователя.

Обычная ошибка Python печатает длинный отчёт с файлами и строками.
Здесь мы создаём ошибку с коротким текстом, который можно показать в консоли.
"""

from __future__ import annotations


class OperationError(Exception):
    """Ожидаемая проблема: настроек нет, файл не найден, сервер не отвечает.

    Такую ошибку не нужно показывать как страшный отчёт Python.
    """


def friendly_message(error: BaseException) -> str:
    """Возвращает короткий текст для любой ошибки."""
    if isinstance(error, OperationError):
        return str(error)
    return f"{type(error).__name__}: {error}"
