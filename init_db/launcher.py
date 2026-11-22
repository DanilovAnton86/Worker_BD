"""Ежедневное меню программы.

Здесь только рабочие действия: обновить список сотрудников, загрузить пароли
и управлять сайтом. Сайт запускается в фоне, поэтому меню остаётся доступным:
обновлять данные можно, не останавливая сайт.

Первичную настройку делает отдельный файл install.py.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from config_loader import (
    BASE_DIR,
    SECTION_WEB,
    get_excel_path,
    get_reports_dir,
    load_config,
)
from operations import (
    clear_web_pid,
    create_database,
    import_passwords,
    pid_alive,
    print_checks,
    read_web_pid,
    run_action,
    sync_ad,
    write_web_pid,
)

MENU_ITEMS = {
    "1": "Обновить данные из AD",
    "2": "Загрузить пароли из Excel",
    "3": "Запустить сайт",
    "4": "Остановить сайт",
    "5": "Перезапустить сайт",
    "6": "Проверить настройки",
}


def get_config():
    """Читает настройки и понятно говорит, если их нет."""
    try:
        return load_config()
    except (OSError, ValueError) as error:
        print(f"\nНе могу прочитать настройки: {error}")
        return None


def print_menu() -> None:
    """Показывает меню и состояние сайта."""
    pid = read_web_pid()
    if pid and pid_alive(pid):
        state = f"работает, номер процесса {pid}"
    else:
        state = "не запущен"

    print("\n" + "=" * 52)
    print("УЧЁТ СОТРУДНИКОВ — РАБОЧЕЕ МЕНЮ")
    print(f"Сайт: {state}")
    print("=" * 52)
    for key, title in MENU_ITEMS.items():
        print(f"  {key}. {title}")
    print("  0. Выход")
    print("=" * 52)


def excel_is_ready(config) -> str | None:
    """Проверяет наличие Excel-файла перед загрузкой паролей."""
    path = get_excel_path(config)
    return None if path.is_file() else f"Не найден файл с паролями: {path}"


def run_update(title: str, action, config, needs_excel: bool = False) -> None:
    """Запускает обновление с блокировкой, копией и журналом."""
    if needs_excel:
        problem = excel_is_ready(config)
        if problem is not None:
            print(f"\nНе могу запустить «{title}»: {problem}")
            return
    run_action(title, action, need_backup=True, config=config)


def start_server(config) -> None:
    """Поднимает сайт отдельным процессом и сразу возвращается в меню."""
    pid = read_web_pid()
    if pid and pid_alive(pid):
        print(f"\nСайт уже работает, номер процесса {pid}.")
        return

    script = BASE_DIR / "server.py"
    if not script.is_file():
        print(f"\nНе найден файл запуска сайта: {script}")
        return

    logs_dir = get_reports_dir(config) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "web.log"

    options = {
        "cwd": str(BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "start_new_session": os.name != "nt",
    }
    if os.name == "nt":
        # Отдельная группа процессов позволяет остановить сайт целиком.
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            options["stdout"] = log_file
            options["stderr"] = subprocess.STDOUT
            process = subprocess.Popen(
                [sys.executable, str(script), "--no-reload"], **options
            )
    except OSError as error:
        print(f"\nНе удалось запустить сайт: {error}")
        return

    write_web_pid(process.pid)
    web = config[SECTION_WEB]
    address = "127.0.0.1" if web["host"] == "0.0.0.0" else web["host"]
    print(f"\nСайт запущен. Откройте: http://{address}:{web['port']}")
    if address != web["host"]:
        print("С другого компьютера откройте по адресу этого сервера.")
    print(f"Записи о работе сайта: {log_path}")


def stop_server() -> None:
    """Останавливает сайт, запущенный из лаунчера."""
    pid = read_web_pid()
    if not pid:
        print("\nСайт не был запущен из лаунчера.")
        return
    if not pid_alive(pid):
        clear_web_pid()
        print("\nСайт уже остановлен.")
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as error:
        print(f"\nНе удалось остановить сайт: {error}")
        return

    clear_web_pid()
    print("\nСайт остановлен.")


def wait_for_enter() -> None:
    """Даёт человеку дочитать результат перед возвратом в меню."""
    try:
        input("\nНажмите Enter, чтобы вернуться в меню...")
    except (EOFError, KeyboardInterrupt):
        print()


def main() -> None:
    """Бесконечный цикл меню, пока пользователь не выберет выход."""
    while True:
        print_menu()
        try:
            choice = input("Выберите пункт (0 — выход): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход из программы.")
            break

        if choice in {"0", "q", "Q", "выход", "Выход"}:
            print("До свидания!")
            break

        if choice not in MENU_ITEMS:
            print("Не понял выбор. Нажмите 1, 2, 3, 4, 5, 6 или 0.")
            continue

        config = get_config()
        if config is None:
            wait_for_enter()
            continue

        if choice == "1":
            run_update(MENU_ITEMS[choice], sync_ad, config)
            wait_for_enter()
        elif choice == "2":
            run_update(
                MENU_ITEMS[choice], import_passwords, config, needs_excel=True
            )
            wait_for_enter()
        elif choice == "3":
            start_server(config)
        elif choice == "4":
            stop_server()
        elif choice == "5":
            stop_server()
            start_server(config)
        elif choice == "6":
            print_checks(config)
            wait_for_enter()


if __name__ == "__main__":
    main()
