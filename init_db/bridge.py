"""Сверка сотрудников с общим списком компании (Active Directory).

Программа берёт из общего списка логин, имя, фамилию и группы вида
«ДОЛЖНОСТЬ_ПРОЕКТ», затем:

    * добавляет новых сотрудников;
    * обновляет имена действующих;
    * уволенных отмечает статусом «Уволен», но не прячет и не отключает
      от их проектов и локаций;
    * связи «проект — должность» пересобирает только у действующих.

Все адреса и пароли берутся из config.ini.
"""

from __future__ import annotations

import datetime
from typing import Any

from config_loader import SECTION_AD, get_reports_dir, load_config
from db import transaction
from errors import OperationError, friendly_message

try:
    from ldap3 import ALL, SUBTREE, Connection, Server

    LDAP_AVAILABLE = True
except ImportError:  # если библиотека не установлена, работаем на примере
    LDAP_AVAILABLE = False

# Технические группы, которые не означают должность и проект.
TECHNICAL_GROUPS = frozenset(
    {"DOMAIN USERS", "DOMAIN", "AD", "EXCHANGE", "WI-FI", "CISCOVPN", "SERVERS"}
)

# Пример данных для проверки без настоящего списка сотрудников компании.
MOCK_USERS: tuple[dict[str, Any], ...] = (
    {
        "login": "a.ivanov",
        "sn": "Иванов",
        "givenName": "Алексей",
        "groups": ["Domain Users", "OP", "OP_MRG"],
        "is_disabled": False,
    },
    {
        "login": "m.petrova",
        "sn": "Петрова",
        "givenName": "Мария",
        "groups": ["SV_VOXYS_RTK_IN", "TR_OZON", "WI-FI"],
        "is_disabled": False,
    },
    {
        "login": "u.volkov",
        "sn": "Волков",
        "givenName": "Юрий",
        "groups": [],
        "is_disabled": True,
    },
    {"login": "op-069$", "sn": "", "givenName": "", "groups": [], "is_disabled": False},
)


def as_text(value: Any) -> str:
    """Приводит значение из списка сотрудников к обычной строке."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value)


def group_names(values: Any) -> list[str]:
    """Достаёт короткие имена групп из полных путей вида CN=...,DC=..."""
    names: list[str] = []
    for value in values or []:
        text = as_text(value).strip()
        if not text:
            continue
        if text.upper().startswith("CN="):
            text = text.split(",", 1)[0][3:]
        names.append(text)
    return names


def parse_ad_groups(groups: list[str], valid_role_codes: set[str]) -> tuple[list[tuple[str, str]], bool]:
    """Разбирает группы «ДОЛЖНОСТЬ_ПРОЕКТ».

    Возвращает список пар (должность, проект) и признак «был мусор».
    Должность ищется по самому длинному известному коду, поэтому названия
    проектов с подчёркиваниями (например, VOXYS_RTK_IN) разбираются правильно.
    """
    relations: list[tuple[str, str]] = []
    has_invalid_groups = False

    for group in groups:
        group_upper = as_text(group).strip().upper()
        if not group_upper or group_upper in TECHNICAL_GROUPS:
            continue

        matched_code = ""
        project_name = ""
        for code in sorted(valid_role_codes, key=len, reverse=True):
            prefix = f"{code}_"
            if group_upper.startswith(prefix):
                matched_code = code
                project_name = group_upper[len(prefix) :].strip()
                break

        if not matched_code or not project_name:
            has_invalid_groups = True
            continue

        relations.append((matched_code, project_name))

    return relations, has_invalid_groups


def read_ldap_users(connection, search_base: str, search_filter: str,
                    is_disabled: bool, page_size: int) -> list[dict[str, Any]]:
    """Читает людей постранично, чтобы не потерять тех, кто дальше первой страницы."""
    users: list[dict[str, Any]] = []

    for entry in connection.paged_search(
        search_base=search_base,
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=["sAMAccountName", "sn", "givenName", "memberOf"],
        paged_size=page_size,
        generator=True,
    ):
        if entry.get("type") != "search_res":
            continue
        attributes = entry.get("attributes", {})
        users.append(
            {
                "login": as_text(attributes.get("sAMAccountName")),
                "sn": as_text(attributes.get("sn")),
                "givenName": as_text(attributes.get("givenName")),
                "groups": [] if is_disabled else group_names(attributes.get("memberOf")),
                "is_disabled": is_disabled,
            }
        )

    return users


def fetch_ad_users(config: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Возвращает список людей из AD или демонстрационный список."""
    settings = config[SECTION_AD]

    if settings["use_mock"] or not LDAP_AVAILABLE:
        print("ВНИМАНИЕ: работает демонстрационный режим, настоящий список не читается.")
        return list(MOCK_USERS)

    active_filter = (
        "(&(objectClass=user)"
        "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
    )
    disabled_filter = (
        "(&(objectClass=user)"
        "(userAccountControl:1.2.840.113556.1.4.803:=2))"
    )

    try:
        server = Server(settings["server"], get_info=ALL)
        connection = Connection(
            server,
            settings["user"],
            settings["password"],
            auto_bind=True,
            auto_referrals=False,
        )
    except Exception as error:
        raise OperationError(
            "Не удалось подключиться к списку сотрудников компании: "
            f"{friendly_message(error)}"
        ) from error

    try:
        print("Поиск действующих сотрудников...")
        users = read_ldap_users(
            connection,
            settings["base_dn"],
            active_filter,
            is_disabled=False,
            page_size=settings["page_size"],
        )

        print("Поиск уволенных сотрудников...")
        disabled_base = settings["disabled_ou"] or settings["base_dn"]
        users.extend(
            read_ldap_users(
                connection,
                disabled_base,
                disabled_filter,
                is_disabled=True,
                page_size=settings["page_size"],
            )
        )
    except Exception as error:
        raise OperationError(
            "Не удалось прочитать список сотрудников компании: "
            f"{friendly_message(error)}"
        ) from error
    finally:
        connection.unbind()

    return users


def write_unmapped_report(reports_dir, unmapped_users: list[str]) -> None:
    """Всегда перезаписывает отчёт, чтобы в нём не осталось старых ошибок."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "unmapped_users.txt"
    lines = [
        "=" * 60,
        "ОТЧЁТ О СОТРУДНИКАХ, КОТОРЫЕ ТРЕБУЮТ ПРОВЕРКИ",
        f"Дата: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        "=" * 60,
        "",
    ]
    if unmapped_users:
        lines.extend(f"- {message}" for message in unmapped_users)
    else:
        lines.append("Ошибок нет.")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Отчёт сохранён: {report_path}")


def sync_ad_to_db(config: dict[str, dict[str, Any]] | None = None) -> None:
    """Основная работа: сверить список сотрудников компании с нашей базой."""
    print("Запуск сверки: общий список сотрудников -> база данных")

    settings = config if config is not None else load_config()
    ad_users = fetch_ad_users(settings)
    if not ad_users:
        raise OperationError("Список сотрудников пуст — база не изменена.")

    try:
        with transaction(config=settings) as database:
            with database.cursor() as cursor:
                cursor.execute("SELECT code, id FROM JobTitle")
                valid_roles = {row["code"].upper(): row["id"] for row in cursor.fetchall()}

                cursor.execute("SELECT name, id FROM Project WHERE is_deleted = 0")
                valid_projects = {row["name"].upper(): row["id"] for row in cursor.fetchall()}

                cursor.execute("SELECT id, login_ad FROM Employee")
                existing_users = {
                    row["login_ad"]: row["id"]
                    for row in cursor.fetchall()
                    if row["login_ad"]
                }

                added_count = 0
                updated_count = 0
                fired_count = 0
                unmapped_users: list[str] = []
                warnings: list[str] = []

                for user in ad_users:
                    login = as_text(user["login"]).lower()
                    second_name = as_text(user["sn"]).strip()
                    first_name = as_text(user["givenName"]).strip()

                    # Компьютеры (логин заканчивается на $) и записи без ФИО не берём.
                    if login.endswith("$") or not second_name or not first_name:
                        if not user["is_disabled"]:
                            unmapped_users.append(
                                f"({login}) — компьютер или пустое ФИО"
                            )
                        continue

                    # Уволенные: только статус. Проекты и локации остаются как были.
                    if user["is_disabled"]:
                        if login in existing_users:
                            cursor.execute(
                                """
                                UPDATE Employee
                                SET id_status = 2, is_deleted = 0
                                WHERE login_ad = %s
                                """,
                                (login,),
                            )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO Employee
                                    (first_name, second_name, login_ad, id_status, is_deleted)
                                VALUES (%s, %s, %s, 2, 0)
                                """,
                                (first_name, second_name, login),
                            )
                            existing_users[login] = cursor.lastrowid
                        fired_count += 1
                        continue

                    # Действующие: разбираем группы и собираем связи с проектами.
                    relations, has_invalid = parse_ad_groups(user["groups"], set(valid_roles))
                    if not relations:
                        unmapped_users.append(
                            f"{second_name} {first_name} ({login}) — нет подходящих групп. "
                            f"Группы: {user['groups']}"
                        )
                        continue

                    final_relations: list[tuple[int, int]] = []
                    for role_code, project_name in relations:
                        role_id = valid_roles.get(role_code)
                        project_id = valid_projects.get(project_name)
                        if role_id and project_id:
                            final_relations.append((role_id, project_id))

                    # Убираем повторы, если в AD одна группа указана дважды.
                    final_relations = list(dict.fromkeys(final_relations))

                    if not final_relations:
                        unmapped_users.append(
                            f"{second_name} {first_name} ({login}) — не найдено ни одной "
                            "подходящей пары «должность — проект»."
                        )
                        continue

                    if has_invalid:
                        warnings.append(
                            f"{second_name} {first_name} ({login}) — часть групп проигнорирована: "
                            f"{user['groups']}"
                        )

                    if login in existing_users:
                        employee_id = existing_users[login]
                        cursor.execute(
                            """
                            UPDATE Employee
                            SET first_name = %s, second_name = %s,
                                id_status = 1, is_deleted = 0
                            WHERE id = %s
                            """,
                            (first_name, second_name, employee_id),
                        )
                        updated_count += 1
                    else:
                        cursor.execute(
                            """
                            INSERT INTO Employee
                                (first_name, second_name, login_ad, id_status, is_deleted)
                            VALUES (%s, %s, %s, 1, 0)
                            """,
                            (first_name, second_name, login),
                        )
                        employee_id = cursor.lastrowid
                        existing_users[login] = employee_id
                        added_count += 1

                    # Старые связи помечаем недействительными, а не удаляем.
                    # Так сохраняется история, но в карточке видны только новые связи.
                    cursor.execute(
                        """
                        UPDATE Project_Employee
                        SET is_deleted = 1, deleted_at = NOW(), deleted_by = NULL
                        WHERE id_emp = %s AND is_deleted = 0
                        """,
                        (employee_id,),
                    )
                    for role_id, project_id in final_relations:
                        cursor.execute(
                            """
                            INSERT INTO Project_Employee
                                (id_project, id_emp, id_job, is_deleted)
                            VALUES (%s, %s, %s, 0)
                            ON DUPLICATE KEY UPDATE
                                is_deleted = 0,
                                deleted_at = NULL,
                                deleted_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (project_id, employee_id, role_id),
                        )
    except Exception as error:
        raise OperationError(f"Ошибка при сверке: {friendly_message(error)}") from error

    print("\n" + "=" * 50)
    print("ИТОГИ СВЕРКИ")
    print("=" * 50)
    print(f"Добавлено новых: {added_count}")
    print(f"Обновлено существующих: {updated_count}")
    print(f"Отмечено как «Уволен»: {fired_count}")

    if warnings:
        print(f"\nПроигнорировано лишних групп ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")

    if unmapped_users:
        print(f"\nТребуют проверки ({len(unmapped_users)}):")
        for message in unmapped_users:
            print(f"  - {message}")

    write_unmapped_report(get_reports_dir(settings), unmapped_users)


if __name__ == "__main__":
    import sys

    from operations import run_action

    sys.exit(0 if run_action("Сверка с AD", sync_ad_to_db, need_backup=True) else 1)
