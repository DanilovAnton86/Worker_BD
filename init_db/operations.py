"""Общие действия установщика и лаунчера.

Здесь собрано то, что должно работать одинаково на Windows и Linux:

    * понятные проверки настроек перед работой;
    * блокировка, чтобы два обновления не пошли одновременно;
    * резервная копия базы перед изменением данных;
    * журнал действий в папке отчётов.

Сами действия (создание таблиц, сверка с AD, загрузка паролей) живут в своих
файлах. Этот модуль только безопасно их запускает.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator

from config_loader import (
    BASE_DIR,
    SECTION_AD,
    SECTION_DATABASE,
    get_excel_path,
    get_reports_dir,
    load_config,
)
from errors import OperationError, friendly_message
from utils import read_excel_quietly

logger = logging.getLogger("crm")
_LOG_READY = False
_LOG_PATH: Path | None = None
STATE_PATH = BASE_DIR / "install.state.json"
WEB_PID_PATH = BASE_DIR / "web_server.pid"


# ---------------------------------------------------------------------------
# Журнал действий
# ---------------------------------------------------------------------------

def setup_logging(config: dict[str, dict[str, Any]] | None = None) -> Path:
    """Готовит файл журнала. Повторные вызовы ничего не ломают."""
    global _LOG_READY, _LOG_PATH
    if _LOG_READY and _LOG_PATH is not None:
        return _LOG_PATH

    settings = config or load_config()
    logs_dir = get_reports_dir(settings) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "operations.log"

    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _LOG_READY = True
    _LOG_PATH = log_path
    return log_path


# ---------------------------------------------------------------------------
# Блокировка от двух одновременных запусков
# ---------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """Проверяет, жив ли процесс с таким номером. Работает в двух системах."""
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Шапка ответа может меняться, поэтому ищем номер в любой строке.
        for line in result.stdout.splitlines():
            if str(pid) in line.split():
                return True
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def operation_lock(name: str, base_dir: Path = BASE_DIR) -> Iterator[None]:
    """Не даёт двум обновлениям идти одновременно.

    Если программа упала, в файле останется старый номер процесса. Мы его
    проверяем и спокойно удаляем просроченную блокировку.
    """
    lock_path = base_dir / f".{name}.lock"
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            old_pid = 0
        if pid_alive(old_pid):
            raise OperationError(
                f"Уже выполняется «{name}» в процессе {old_pid}. "
                "Дождитесь окончания или остановите тот процесс."
            )
        lock_path.unlink(missing_ok=True)

    try:
        descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
        )
        os.write(descriptor, str(os.getpid()).encode("utf-8"))
        os.close(descriptor)
    except FileExistsError as error:
        raise OperationError(
            f"Другое обновление уже началось: {lock_path}"
        ) from error

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Резервная копия
# ---------------------------------------------------------------------------

def backup_database(config: dict[str, dict[str, Any]]) -> Path | None:
    """Сохраняет копию базы перед изменением данных.

    Если программа для копирования не установлена, предупреждаем и идём дальше.
    Если она есть, но сработала с ошибкой — останавливаем обновление.
    """
    executable = os.getenv("DUMP_COMMAND") or (
        shutil.which("mariadb-dump") or shutil.which("mysqldump")
    )
    if not executable:
        message = (
            "Не найдена программа для резервной копии "
            "(mariadb-dump или mysqldump). Продолжаем без копии. "
            "Установите её или укажите путь в DUMP_COMMAND."
        )
        print("ВНИМАНИЕ: " + message)
        logger.warning(message)
        return None

    settings = config[SECTION_DATABASE]
    backups_dir = get_reports_dir(config) / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_path = backups_dir / f"backup_{stamp}.sql"

    environment = os.environ.copy()
    # Пароль не подаём в командной строке,
    # чтобы он не светился в списке процессов.
    environment["MYSQL_PWD"] = str(settings["password"])

    command = [
        executable,
        f"--host={settings['host']}",
        f"--port={settings['port']}",
        f"--user={settings['user']}",
        "--single-transaction",
        "--default-character-set=utf8mb4",
        settings["name"],
    ]

    with dump_path.open("wb") as output:
        result = subprocess.run(
            command, stdout=output, stderr=subprocess.PIPE,
            env=environment, check=False,
        )

    if result.returncode != 0:
        dump_path.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(f"Не удалось сделать резервную копию: {detail}")

    print(f"Резервная копия: {dump_path}")
    logger.info("Резервная копия сохранена: %s", dump_path)
    return dump_path


# ---------------------------------------------------------------------------
# Проверки без изменения данных
# ---------------------------------------------------------------------------

def check_database(config: dict[str, dict[str, Any]]) -> list[str]:
    """Пробует подключиться к базе и прочитать один номер."""
    from db import get_connection

    try:
        connection = get_connection(config=config)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
        finally:
            connection.close()
        return []
    except Exception as error:
        return [f"База данных: {friendly_message(error)}"]


def check_ad(config: dict[str, dict[str, Any]]) -> list[str]:
    """Проверяет подключение к общему списку сотрудников только чтением."""
    settings = config[SECTION_AD]
    if settings["use_mock"]:
        return ["Общий список сотрудников: включён демонстрационный режим"]

    try:
        from ldap3 import BASE, Connection, Server
    except ImportError:
        return ["Общий список сотрудников: не установлена библиотека ldap3"]

    try:
        server = Server(settings["server"])
        connection = Connection(
            server, settings["user"], settings["password"], auto_bind=True
        )
        connection.search(
            search_base=settings["base_dn"],
            search_filter="(objectClass=*)",
            search_scope=BASE,
            attributes=["objectClass"],
            size_limit=1,
        )
        connection.unbind()
        return []
    except Exception as error:
        return [f"Общий список сотрудников: {friendly_message(error)}"]


def check_excel(config: dict[str, dict[str, Any]]) -> list[str]:
    """Проверяет, что файл с паролями есть и читается."""
    path = get_excel_path(config)
    if not path.is_file():
        return [f"Excel-файл не найден: {path}"]
    try:
        read_excel_quietly(path, dtype=str, nrows=1)
        return []
    except Exception as error:
        return [f"Excel-файл: {friendly_message(error)}"]


def check_templates() -> list[str]:
    """Проверяет, что все страницы открываются без ошибок."""
    from jinja2 import Environment, FileSystemLoader

    templates_dir = BASE_DIR / "templates"
    try:
        environment = Environment(loader=FileSystemLoader(str(templates_dir)))
        for path in sorted(Path(templates_dir).glob("*.html")):
            environment.get_template(path.name)
        return []
    except Exception as error:
        return [f"Страницы сайта: {friendly_message(error)}"]


def print_checks(config: dict[str, dict[str, Any]]) -> bool:
    """Показывает результаты всех проверок.

    Возвращает True, если всё хорошо.
    """
    print("\nПРОВЕРКА НАСТРОЕК")
    print("-" * 46)
    reports = [
        ("База данных", check_database(config)),
        ("Общий список сотрудников", check_ad(config)),
        ("Excel-файл", check_excel(config)),
        ("Страницы сайта", check_templates()),
    ]
    clean = True
    for title, problems in reports:
        if not problems:
            print(f"  [OK] {title}")
            continue
        clean = False
        for problem in problems:
            if problem.startswith("Общий список сотрудников: включён"):
                print(f"  [!] {problem}")
            else:
                print(f"  [НЕТ] {problem}")
    print("-" * 46)
    return clean


# ---------------------------------------------------------------------------
# Запуск действий
# ---------------------------------------------------------------------------

def create_database(config: dict[str, dict[str, Any]]) -> None:
    """Создаёт базу и таблицы."""
    from init_db import init_database

    init_database(config)


def sync_ad(config: dict[str, dict[str, Any]]) -> None:
    """Сверяет список сотрудников компании с нашей базой."""
    from bridge import sync_ad_to_db

    sync_ad_to_db(config)


def import_passwords(config: dict[str, dict[str, Any]]) -> None:
    """Загружает пароли из Excel."""
    from stage_import_passwords import main as import_main

    import_main(config)


def run_action(
    title: str,
    action: Callable[[dict[str, dict[str, Any]]], None],
    *,
    need_backup: bool = False,
    config: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Безопасно выполняет одно действие и возвращает True при успехе."""
    try:
        settings = config or load_config()
        log_path = setup_logging(settings)
        with operation_lock(title):
            if need_backup:
                backup_database(settings)
            logger.info("Начало: %s", title)
            action(settings)
            logger.info("Успех: %s", title)
        print(f"\nГотово: {title}.")
        print(f"Журнал: {log_path}")
        return True
    except Exception as error:
        message = friendly_message(error)
        try:
            logger.error("Ошибка: %s — %s", title, message)
        except Exception:
            pass
        print(f"\nОшибка при «{title}»: {message}")
        return False


# ---------------------------------------------------------------------------
# Состояние установки и номер процесса сайта
# ---------------------------------------------------------------------------

def read_state() -> dict[str, Any]:
    """Читает заметки о том, что уже сделано при установке."""
    try:
        return dict(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def write_state(**values: Any) -> None:
    """Записывает заметки об установке."""
    state = read_state()
    state.update(values)
    state["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_web_pid() -> int:
    """Возвращает номер процесса сайта из файла-памятки."""
    try:
        return int(WEB_PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_web_pid(pid: int) -> None:
    """Запоминает номер процесса сайта."""
    WEB_PID_PATH.write_text(str(pid), encoding="utf-8")


def clear_web_pid() -> None:
    """Убирает памятку об остановленном сайте."""
    WEB_PID_PATH.unlink(missing_ok=True)
