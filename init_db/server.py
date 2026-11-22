"""Отдельный запуск сайта.

Обычно сайт поднимает лаунчер. Этот файл нужен для служб Windows и Linux,
чтобы сайт стартовал сам при включении компьютера.

Пример для Linux (systemd):
    python3 /путь/к/папке/server.py --no-reload

Пример для Windows (NSSM):
    python.exe C:\\путь\\к\\папке\\server.py --no-reload
"""

from __future__ import annotations

import argparse

from config_loader import SECTION_WEB, load_config


def parse_args() -> argparse.Namespace:
    """Читает дополнительные подсказки из командной строки."""
    parser = argparse.ArgumentParser(
        description="Запуск сайта учёта сотрудников"
    )
    parser.add_argument(
        "--host", help="адрес, который переопределяет настройку"
    )
    parser.add_argument(
        "--port", type=int, help="порт, который переопределяет настройку"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="не перезапускать сайт при правке файлов (для рабочего сервера)",
    )
    return parser.parse_args()


def main() -> None:
    """Поднимает сайт с настройками из config.ini."""
    import uvicorn

    arguments = parse_args()
    settings = load_config()[SECTION_WEB]

    host = arguments.host or settings["host"]
    port = arguments.port or settings["port"]
    reload = settings["reload"] and not arguments.no_reload

    print(f"Сайт открывается по адресу: http://{host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
