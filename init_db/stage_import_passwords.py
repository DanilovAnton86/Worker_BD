"""Заливка паролей и недостающих данных из Excel-файла.

Скрипт ищет человека по логину, сверяет фамилию и имя, затем обновляет
пароль. Если логин в файле немного отличается от логина в базе, но ФИО
совпало, пароль всё равно обновляется, а логин остаётся из общего списка.

Поведение намеренно оставлено прежним: пароли хранятся и пишутся в отчёты
в открытом виде. Это нужно текущему этапу проверки работы программы.

Имя файла и все подключения берутся из config.ini.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from config_loader import get_excel_path, get_reports_dir, load_config
from db import transaction
from errors import OperationError, friendly_message
from utils import clean_birthday, read_excel_quietly, sanitize_value

# Раскладка клавиатуры: кириллицей набрали то, что хотели набрать латиницей.
CYRILLIC_TO_LATIN = {
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
}

# Слова из Excel, которые означают «пусто».
EMPTY_WORDS = frozenset({"nan", "none", "null", ""})

# Статус из Excel -> номер статуса в базе.
STATUS_IDS = {"работает": 1, "активен": 1, "уволен": 2, "декрет": 3}


def as_text(value: Any) -> str:
    """Убирает NaN, None и лишние пробелы из значения Excel."""
    return sanitize_value(value)


def fix_login(login_value: Any) -> str:
    """Приводит логин к нижнему регистру и исправляет русскую раскладку."""
    login = as_text(login_value).lower()
    for cyrillic, latin in CYRILLIC_TO_LATIN.items():
        login = login.replace(cyrillic, latin)
    return login


def is_valid_login(login: str) -> bool:
    """Пропускаем только логины из латинских букв и точек."""
    return bool(re.fullmatch(r"[a-z.]+", login)) if login else False


def clean_phone(phone_value: Any) -> str | None:
    """Оставляет только цифры телефона."""
    phone = as_text(phone_value)
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[:15] if digits else None


def get_status_id(status_value: Any) -> int | None:
    """Превращает слово «Уволен» и т.п. в номер статуса."""
    return STATUS_IDS.get(as_text(status_value).lower())


def get_or_create_location(cursor, location_name: Any) -> int | None:
    """Находит локацию в справочнике или добавляет новую."""
    name = as_text(location_name)
    if not name:
        return None
    cursor.execute("SELECT id FROM Location WHERE name = %s", (name,))
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute("INSERT INTO Location (name) VALUES (%s)", (name,))
    return cursor.lastrowid


def write_reports(reports_dir: Path, reports: dict[str, list[str]]) -> None:
    """Сохраняет отчёты. Пароли в них остаются намеренно."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%d.%m.%Y_%H-%M")

    for report_name, lines in reports.items():
        if not lines:
            continue
        report_path = reports_dir / f"report_{report_name}_{timestamp}.txt"
        report_path.write_text("\n".join(lines), encoding="utf-8")

    readme_path = reports_dir / "README_IMPORT.txt"
    readme_path.write_text(
        "\n".join(
            [
                "=" * 60,
                "ЧТО ЗНАЧАТ ОТЧЁТЫ ИМПОРТА ПАРОЛЕЙ",
                "=" * 60,
                "",
                "report_success — кому обновили пароль или кого создали заново.",
                "report_errors — логины или ФИО, которые нужно проверить вручную.",
                "report_warnings — мелкие неточности, работу они не ломают.",
                "report_REVIEW — похожие логины. Пароль обновлён, логин в базе не менялся.",
                "",
                "Отчёты содержат пароли в открытом виде: это нужно для проверки работы.",
                "Не передавайте их посторонним и удалите после проверки.",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Отчёты сохранены в папке: {reports_dir}")


def main(config: dict[str, dict[str, Any]] | None = None) -> None:
    """Основная работа: прочитать Excel и внести данные в базу."""
    print("Запуск: загрузка паролей из Excel")

    settings = config if config is not None else load_config()
    excel_path = get_excel_path(settings)
    if not excel_path.is_file():
        raise OperationError(f"Файл не найден: {excel_path}")

    dataframe = read_excel_quietly(excel_path, dtype=str)
    dataframe.columns = [str(column).strip().lower() for column in dataframe.columns]

    # Названия колонок могут немного отличаться — ищем их по ключевым словам.
    column_status = next((c for c in dataframe.columns if "статус" in c), None)
    column_login = next((c for c in dataframe.columns if "логин" in c and "ad" in c), None)
    column_password = next((c for c in dataframe.columns if "пароль" in c and "ad" in c), None)
    column_second = next((c for c in dataframe.columns if "фамилия" in c), None)
    column_first = next((c for c in dataframe.columns if "имя" in c and "фамилия" not in c), None)
    column_curname = next((c for c in dataframe.columns if "отчество" in c), None)
    column_project = next((c for c in dataframe.columns if "проект" in c), None)
    column_location = next((c for c in dataframe.columns if "локация" in c), None)
    column_phone = next((c for c in dataframe.columns if "телефон" in c), None)
    column_birthday = next((c for c in dataframe.columns if "рождения" in c), None)

    if not column_login:
        raise OperationError(
            "В Excel-файле не найдена колонка с логином AD. "
            f"Доступные колонки: {dataframe.columns.tolist()}"
        )

    reports: dict[str, list[str]] = {
        "success": [],
        "errors": [],
        "warnings": [],
        "REVIEW": [],
    }
    added = 0
    updated_passwords = 0
    skipped_archive = 0
    skipped_empty = 0

    try:
        with transaction(config=settings) as database:
            with database.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, login_ad, first_name, second_name, id_status, is_deleted
                    FROM Employee
                    """
                )
                users_by_login = {
                    row["login_ad"].lower(): row
                    for row in cursor.fetchall()
                    if row["login_ad"]
                }

                cursor.execute("SELECT id, name FROM Project")
                projects_by_name = {
                    row["name"].upper(): row["id"] for row in cursor.fetchall()
                }

                cursor.execute("SELECT id FROM JobTitle WHERE code = 'OP' LIMIT 1")
                operator_job = cursor.fetchone()
                operator_job_id = operator_job["id"] if operator_job else 7

                processed_in_this_run: set[str] = set()

                for _, row in dataframe.iterrows():
                    raw_login = row.get(column_login)
                    if not as_text(raw_login):
                        skipped_empty += 1
                        continue

                    excel_password = as_text(row.get(column_password))
                    excel_second = as_text(row.get(column_second))
                    excel_first = as_text(row.get(column_first))
                    excel_curname = as_text(row.get(column_curname))
                    excel_project = as_text(row.get(column_project))
                    excel_location = as_text(row.get(column_location))

                    # Пропускаем строки-описания из шапки файла.
                    if not excel_second and not excel_first and not excel_password:
                        continue

                    login = fix_login(raw_login)
                    if not is_valid_login(login):
                        reports["errors"].append(
                            f"КРИВОЙ ЛОГИН: {raw_login} -> {login} | "
                            f"ФИО: {excel_second} {excel_first} | Пароль: {excel_password}"
                        )
                        continue

                    if not excel_password or excel_password.lower() in EMPTY_WORDS:
                        reports["warnings"].append(
                            f"НЕТ ПАРОЛЯ: {login} ({excel_second} {excel_first}) — пропущен"
                        )
                        continue

                    # Точное совпадение логина.
                    if login in users_by_login:
                        database_user = users_by_login[login]

                        if database_user["is_deleted"] == 1:
                            skipped_archive += 1
                            continue

                        database_second = (database_user["second_name"] or "").lower()
                        database_first = (database_user["first_name"] or "").lower()
                        if database_second != excel_second.lower() or database_first != excel_first.lower():
                            reports["errors"].append(
                                f"КОНФЛИКТ ФИО (логин {login}): в базе "
                                f"{database_second} {database_first}, в файле "
                                f"{excel_second} {excel_first}. Пароль НЕ обновлён."
                            )
                            continue

                        cursor.execute(
                            """
                            UPDATE Employee
                            SET password_ad = %s, password_email = %s
                            WHERE id = %s
                            """,
                            (excel_password, excel_password, database_user["id"]),
                        )
                        updated_passwords += 1
                        reports["success"].append(
                            f"ОБНОВЛЁН ПАРОЛЬ: {login} ({excel_second} {excel_first})"
                        )

                        status_id = get_status_id(row.get(column_status))
                        if status_id is not None and database_user["id_status"] != status_id:
                            cursor.execute(
                                "UPDATE Employee SET id_status = %s WHERE id = %s",
                                (status_id, database_user["id"]),
                            )
                            database_user["id_status"] = status_id

                        # Уволенным не меняем локации: они должны остаться на месте.
                        if database_user["id_status"] == 2:
                            continue

                        location_id = get_or_create_location(cursor, excel_location)
                        if location_id:
                            # Добавляем локацию, но ничего не удаляем — так ничего не потеряем.
                            cursor.execute(
                                """
                                INSERT IGNORE INTO Location_Employee (id_loc, id_emp)
                                VALUES (%s, %s)
                                """,
                                (location_id, database_user["id"]),
                            )
                        continue

                    # Похожий логин: ищем по последней части логина и совпадению ФИО.
                    possible_match = None
                    login_part = login.split(".")[-1] if "." in login else login
                    for database_login, database_user in users_by_login.items():
                        if login_part not in database_login:
                            continue
                        database_second = (database_user["second_name"] or "").lower()
                        database_first = (database_user["first_name"] or "").lower()
                        if database_second == excel_second.lower() and database_first == excel_first.lower():
                            possible_match = (database_login, database_user)
                            break

                    if possible_match:
                        old_login, database_user = possible_match
                        if database_user["is_deleted"] == 1:
                            skipped_archive += 1
                            continue

                        # ФИО совпало — пароль берём из файла, логин оставляем из AD.
                        cursor.execute(
                            """
                            UPDATE Employee
                            SET password_ad = %s, password_email = %s
                            WHERE id = %s
                            """,
                            (excel_password, excel_password, database_user["id"]),
                        )
                        updated_passwords += 1
                        reports["REVIEW"].append(
                            f"ПОХОЖИЙ ЛОГИН (пароль обновлён): в файле {login} "
                            f"({excel_second} {excel_first}), в базе {old_login}. "
                            "Логин оставлен из AD."
                        )
                        continue

                    if login in processed_in_this_run:
                        reports["errors"].append(
                            f"ДУБЛЬ В ФАЙЛЕ: логин {login} ({excel_second} {excel_first}) "
                            "уже встречался. Вторая строка пропущена."
                        )
                        continue

                    # Новый сотрудник. Если в файле нет имени или фамилии, ставим заглушку.
                    final_second = excel_second or "[Фамилия]"
                    final_first = excel_first or "[Имя]"
                    final_curname = excel_curname or None
                    if final_second == "[Фамилия]" or final_first == "[Имя]":
                        reports["warnings"].append(
                            f"НЕПОЛНОЕ ФИО: {login} создан как {final_second} {final_first}"
                        )

                    cursor.execute(
                        """
                        INSERT INTO Employee (
                            first_name, second_name, curname, phone, birthday,
                            login_ad, password_ad, password_email, id_status, is_deleted
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                        """,
                        (
                            final_first,
                            final_second,
                            final_curname,
                            clean_phone(row.get(column_phone)),
                            clean_birthday(row.get(column_birthday)),
                            login,
                            excel_password,
                            excel_password,
                            get_status_id(row.get(column_status)) or 1,
                        ),
                    )
                    new_id = cursor.lastrowid
                    added += 1
                    processed_in_this_run.add(login)
                    reports["success"].append(
                        f"СОЗДАН НОВЫЙ: {login} ({final_second} {final_first})"
                    )

                    project_id = projects_by_name.get(excel_project.upper())
                    if project_id:
                        cursor.execute(
                            """
                            INSERT IGNORE INTO Project_Employee
                                (id_project, id_emp, id_job)
                            VALUES (%s, %s, %s)
                            """,
                            (project_id, new_id, operator_job_id),
                        )

                    location_id = get_or_create_location(cursor, excel_location)
                    if location_id:
                        cursor.execute(
                            """
                            INSERT IGNORE INTO Location_Employee (id_loc, id_emp)
                            VALUES (%s, %s)
                            """,
                            (location_id, new_id),
                        )
    except Exception as error:
        raise OperationError(f"Ошибка при загрузке: {friendly_message(error)}") from error

    print("\n" + "=" * 50)
    print("ИТОГИ ЗАГРУЗКИ ПАРОЛЕЙ")
    print("=" * 50)
    print(f"Обновлено паролей: {updated_passwords}")
    print(f"Создано новых сотрудников: {added}")
    print(f"Пропущено из архива: {skipped_archive}")
    print(f"Пропущено без логина: {skipped_empty}")
    print(f"Требуют проверки: {len(reports['REVIEW'])}")

    write_reports(get_reports_dir(settings), reports)


if __name__ == "__main__":
    import sys

    from operations import run_action

    sys.exit(0 if run_action("Загрузка паролей", main, need_backup=True) else 1)
