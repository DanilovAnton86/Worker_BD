"""Чтение настроек из файла config.ini.

Все адреса, имена и пароли лежат в одном месте — в config.ini
рядом с программой. В коде они не повторяются. Если настроек нет,
программа сразу скажет, что делать.

Подробности:
    * configparser работает только с обычным текстом,
      дополнительные пакеты не нужны;
    * interpolation=None позволяет использовать в паролях значок %;
    * переменные окружения (например, DB_HOST) могут переопределить файл
      настроек — это удобно при запуске на другом компьютере.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any

# Папка, где лежит эта программа. Все относительные пути считаются от неё.
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.ini"

# Имена разделов настроек. Используем их везде, чтобы не ошибиться в написании.
SECTION_DATABASE = "database"
SECTION_AD = "activedirectory"
SECTION_EXCEL = "excel"
SECTION_WEB = "web"
SECTION_REPORTS = "reports"

# Без этих настроек программа не сможет работать.
REQUIRED_SETTINGS: dict[str, tuple[str, ...]] = {
    SECTION_DATABASE: ("host", "port", "user", "password", "name"),
    SECTION_AD: ("server", "user", "password", "base_dn"),
    SECTION_EXCEL: ("file",),
    SECTION_WEB: ("secret_key",),
}

# Значения, которые должны быть числами.
INTEGER_SETTINGS: tuple[tuple[str, str], ...] = (
    (SECTION_DATABASE, "port"),
    (SECTION_AD, "page_size"),
    (SECTION_WEB, "port"),
)

# Значения, которые должны быть да/нет.
BOOLEAN_SETTINGS: tuple[tuple[str, str], ...] = (
    (SECTION_AD, "use_mock"),
    (SECTION_WEB, "reload"),
)

# Значения по умолчанию для необязательных настроек.
DEFAULT_VALUES: dict[tuple[str, str], str] = {
    (SECTION_DATABASE, "charset"): "utf8mb4",
    (SECTION_AD, "disabled_ou"): "",
    (SECTION_AD, "use_mock"): "false",
    (SECTION_AD, "page_size"): "1000",
    (SECTION_WEB, "host"): "127.0.0.1",
    (SECTION_WEB, "port"): "8080",
    (SECTION_WEB, "reload"): "true",
    (SECTION_REPORTS, "dir"): ".",
}

# Старые имена переменных окружения оставляем рабочими.
ENVIRONMENT_OVERRIDES: dict[tuple[str, str], str] = {
    (SECTION_DATABASE, "host"): "DB_HOST",
    (SECTION_DATABASE, "port"): "DB_PORT",
    (SECTION_DATABASE, "user"): "DB_USER",
    (SECTION_DATABASE, "password"): "DB_PASSWORD",
    (SECTION_DATABASE, "name"): "DB_NAME",
    (SECTION_AD, "server"): "AD_SERVER",
    (SECTION_AD, "user"): "AD_USER",
    (SECTION_AD, "password"): "AD_PASSWORD",
    (SECTION_AD, "base_dn"): "AD_BASE_DN",
    (SECTION_EXCEL, "file"): "EXCEL_FILE",
}

TRUE_VALUES = frozenset({"1", "yes", "true", "on"})
FALSE_VALUES = frozenset({"0", "no", "false", "off"})


def _as_integer(raw_value: str, setting_name: str) -> int:
    """Превращает текст в число и понятно объясняет ошибку."""
    try:
        return int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Настройка {setting_name} должна быть числом, а не «{raw_value}»"
        ) from error


def _as_boolean(raw_value: str, setting_name: str) -> bool:
    """Превращает да/нет в true/false."""
    prepared = str(raw_value).strip().lower()
    if prepared in TRUE_VALUES:
        return True
    if prepared in FALSE_VALUES:
        return False
    raise ValueError(
        f"Настройка {setting_name} должна быть да/нет, а не «{raw_value}»"
    )


def load_config(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Читает config.ini и возвращает словарь словарей.

    Пример результата: config["database"]["host"], config["web"]["port"].
    """
    if path is not None:
        config_path = Path(path)
    else:
        config_path = Path(os.getenv("APP_CONFIG", str(DEFAULT_CONFIG_PATH)))

    if not config_path.is_file():
        raise FileNotFoundError(
            f"Не найден файл настроек: {config_path}.\n"
            "Скопируйте config.example.ini в config.ini "
            "и заполните свои значения."
        )

    parser = configparser.ConfigParser(interpolation=None)
    # utf-8-sig читает файл и с меткой BOM (Блокнот на Windows), и без неё.
    with config_path.open("r", encoding="utf-8-sig") as config_file:
        parser.read_file(config_file)

    # Имена разделов приводим к нижнему регистру:
    # [Database] и [database] — одно и то же.
    config: dict[str, dict[str, Any]] = {
        section.strip().lower(): dict(parser.items(section, raw=True))
        for section in parser.sections()
    }

    # Переменные окружения имеют приоритет над файлом — так проще менять
    # настройки на другом компьютере, не меняя сам файл.
    for (section, key), variable_name in ENVIRONMENT_OVERRIDES.items():
        if variable_name in os.environ:
            config.setdefault(section, {})[key] = os.environ[variable_name]

    # Убираем лишние пробелы вокруг значений и подставляем умолчания.
    for values in config.values():
        for key, value in values.items():
            values[key] = str(value).strip()
    for (section, key), default_value in DEFAULT_VALUES.items():
        config.setdefault(section, {}).setdefault(key, default_value)

    missing = [
        f"[{section}] {key}"
        for section, keys in REQUIRED_SETTINGS.items()
        for key in keys
        if not config.get(section, {}).get(key)
    ]
    if missing:
        raise ValueError(
            "В файле настроек не заполнены: " + ", ".join(missing)
        )

    for section, key in INTEGER_SETTINGS:
        title = f"[{section}] {key}"
        config[section][key] = _as_integer(config[section][key], title)
    for section, key in BOOLEAN_SETTINGS:
        title = f"[{section}] {key}"
        config[section][key] = _as_boolean(config[section][key], title)

    return config


def get_excel_path(
    config: dict[str, dict[str, Any]], base_dir: Path = BASE_DIR
) -> Path:
    """Возвращает полный путь к Excel-файлу из настроек."""
    excel_path = Path(config[SECTION_EXCEL]["file"])
    return excel_path if excel_path.is_absolute() else base_dir / excel_path


def get_reports_dir(
    config: dict[str, dict[str, Any]], base_dir: Path = BASE_DIR
) -> Path:
    """Возвращает папку, куда складываются отчёты."""
    reports_dir = Path(config.get(SECTION_REPORTS, {}).get("dir", "."))
    return reports_dir if reports_dir.is_absolute() else base_dir / reports_dir
