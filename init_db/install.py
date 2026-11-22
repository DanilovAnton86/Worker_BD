"""Мастер первичной установки.

Запускайте этот файл один раз, когда программу только что перенесли
на новый компьютер. Мастер:

    1. поможет заполнить config.ini;
    2. проверит базу, общий список сотрудников и Excel-файл;
    3. спросит, что сделать сейчас: создать базу, сверить список,
       загрузить пароли или всё сразу.

Повторный запуск ничего не портит: если настройки уже есть, их можно
оставить без изменений. Ежедневную работу делает launcher.py.
"""

from __future__ import annotations

import configparser
import secrets
from pathlib import Path

from config_loader import (
    BASE_DIR,
    DEFAULT_CONFIG_PATH,
    load_config,
)
from operations import (
    create_database,
    import_passwords,
    print_checks,
    run_action,
    sync_ad,
    write_state,
)

EXAMPLE_PATH = BASE_DIR / "config.example.ini"

# Клавиша -> (название, функция, нужна ли резервная копия).
ACTIONS = {
    "db": ("Создание базы данных", create_database, False),
    "ad": ("Сверка с AD", sync_ad, True),
    "excel": ("Загрузка паролей", import_passwords, True),
}


def ask(question: str, default: str = "") -> str:
    """Спрашивает ответ и подставляет прежнее значение по нажатию Enter."""
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return answer or default


def read_ini(path: Path) -> configparser.ConfigParser:
    """Читает ini-файл; BOM и регистр разделов не мешают."""
    parser = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8-sig") as source:
        parser.read_file(source)
    return parser


def fill_config(
    old: configparser.ConfigParser | None,
) -> configparser.ConfigParser:
    """Задаёт вопросы по всем настройкам из файла-образца."""
    template = read_ini(EXAMPLE_PATH)
    result = configparser.ConfigParser(interpolation=None)

    for section in template.sections():
        result[section] = {}
        for key, example_value in template.items(section, raw=True):
            previous = ""
            if old is not None and old.has_option(section, key):
                previous = old.get(section, key, raw=True)
            value = ask(f"{section}.{key}", previous or example_value)

            # Ключ сессии должен быть случайным, а не словом из образца.
            is_secret = section.lower() == "web" and key == "secret_key"
            if is_secret and value == example_value:
                value = secrets.token_urlsafe(32)

            result[section][key] = value
    return result


def ensure_config(path: Path) -> dict:
    """Готовит config.ini и возвращает проверенные настройки."""
    if not EXAMPLE_PATH.is_file():
        raise SystemExit(f"Не найден файл-образец: {EXAMPLE_PATH}")

    if path.is_file():
        answer = ask(
            "Настройки уже есть. 1 — оставить, 2 — заполнить заново", "1"
        )
        if answer == "1":
            return load_config(path)
        old = read_ini(path)
    else:
        print("\nФайла настроек нет. Заполним его по образцу.")
        old = None

    filled = fill_config(old)
    with path.open("w", encoding="utf-8") as target:
        target.write("# Настройки программы. Заполнены мастером установки.\n")
        filled.write(target)
    print(f"\nНастройки сохранены: {path}")
    return load_config(path)


def choose_steps(database_ok: bool) -> list[str]:
    """Спрашивает, какие действия выполнить прямо сейчас."""
    print("\nЧТО СДЕЛАТЬ СЕЙЧАС")
    print("-" * 46)
    print("  1. Создать базу данных и таблицы")
    print("  2. Сверить список сотрудников (AD)")
    print("  3. Загрузить пароли из Excel")
    print("  4. Всё по порядку")
    print("  0. Только проверить настройки, ничего не менять")
    answer = ask("Выберите пункт", "4" if database_ok else "1")

    if answer == "0":
        steps: list[str] = []
    elif answer == "1":
        steps = ["db"]
    elif answer == "2":
        steps = ["ad"]
    elif answer == "3":
        steps = ["excel"]
    elif answer == "4":
        steps = ["db", "ad", "excel"]
    else:
        print("Не понял выбор. Ничего не менял.")
        return []

    # Без таблиц сверка и загрузка не работают — добавим создание базы.
    if not database_ok and steps and steps[0] != "db":
        steps.insert(0, "db")
        print("Сначала создам базу: без неё другие шаги не работают.")
    return steps


def main() -> None:
    """Проводит мастера установки от начала до конца."""
    print("=" * 52)
    print("ПЕРВИЧНАЯ УСТАНОВКА")
    print("=" * 52)

    try:
        config = ensure_config(DEFAULT_CONFIG_PATH)
    except (OSError, ValueError, configparser.Error) as error:
        print(f"\nНе удалось подготовить настройки: {error}")
        print("Исправьте config.ini вручную и запустите установку снова.")
        raise SystemExit(1)

    clean = print_checks(config)
    steps = choose_steps(clean)
    if not steps:
        write_state(installed=True, steps="только проверка")
        print("\nУстановка завершена. Ничего не изменено.")
        return

    done = []
    for step in steps:
        title, action, need_backup = ACTIONS[step]
        if run_action(title, action, need_backup=need_backup, config=config):
            done.append(title)

    write_state(installed=True, steps=", ".join(done) if done else "ничего")
    print("\n" + "=" * 52)
    print("УСТАНОВКА ЗАВЕРШЕНА")
    print("=" * 52)
    print("Для ежедневной работы запускайте launcher.py.")


if __name__ == "__main__":
    main()
