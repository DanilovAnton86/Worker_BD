"""Подключение к базе данных MariaDB.

Раньше каждая программа создавала подключение сама и повторяла одни и те же
настройки. Теперь подключение делается только здесь — из config.ini.

Функции:
    get_connection() — обычное подключение;
    transaction() — подключение, которое само сохранит изменения или отменит их
                    при ошибке;
    quote_identifier() — безопасное обрамление имени базы данных.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from config_loader import SECTION_DATABASE, load_config


def get_connection(
    include_db: bool = True, config: dict[str, dict[str, Any]] | None = None
):
    """Открывает подключение к MariaDB.

    include_db=False нужен только тогда, когда мы создаём саму базу данных:
    подключиться к ещё не существующей базе нельзя.
    """
    settings = (
        config[SECTION_DATABASE]
        if config is not None
        else load_config()[SECTION_DATABASE]
    )

    connection_parameters = {
        "host": settings["host"],
        "port": settings["port"],
        "user": settings["user"],
        "password": settings["password"],
        "charset": settings.get("charset", "utf8mb4"),
        "cursorclass": DictCursor,
        "autocommit": False,  # изменения сохраняются только после commit()
    }
    if include_db:
        connection_parameters["database"] = settings["name"]

    return pymysql.connect(**connection_parameters)


@contextmanager
def transaction(
    include_db: bool = True, config: dict[str, dict[str, Any]] | None = None
) -> Iterator[Any]:
    """Совершает группу изменений: всё вместе или ничего.

    Если внутри блока произойдёт ошибка, все сделанные изменения отменяются.
    Это важно, чтобы база не осталась «наполовину обновлённой».
    """
    connection = get_connection(include_db=include_db, config=config)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def quote_identifier(name: str) -> str:
    """Обрамляет имя базы данных символами ` и не даёт подсунуть лишнее."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", str(name)):
        raise ValueError(f"Недопустимое имя базы данных: {name!r}")
    return f"`{name}`"
