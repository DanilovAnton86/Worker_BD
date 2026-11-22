"""Правила доступа: кто что может видеть, менять и назначать.

Раньше эти правила были разбросаны по четырём местам main.py и противоречили
друг другу. Здесь они собраны вместе. Основное правило:

    человек видит только младшие должности и только на своих проектах.

Дополнения:
    * IT и суперпользователь видят и меняют всё;
    * администратор (ADM) видит всех, кроме ADM и IT, на всех проектах;
    * OPERATOR (OP) не может входить в систему.
"""

from __future__ import annotations

from typing import Any

# Кого видит каждая должность.
VIEW_LEVELS: dict[str, frozenset[str]] = {
    "ST": frozenset({"OP"}),
    "TR": frozenset({"OP", "ST"}),
    "SV": frozenset({"OP", "ST", "TR"}),
    "RP": frozenset({"OP", "ST", "TR", "SV"}),
    # Рекрутер и СММ в системе приравнены к старшему специалисту.
    "RKT": frozenset({"OP"}),
    "SMM": frozenset({"OP"}),
}

# Кто может менять чужие карточки. Старший и тренер — только чтение.
EDIT_LEVELS: dict[str, frozenset[str]] = {
    "SV": VIEW_LEVELS["SV"],
    "RP": VIEW_LEVELS["RP"],
}

# Какие должности можно назначать при редактировании проекта.
ASSIGN_LEVELS: dict[str, frozenset[str]] = {
    "SV": frozenset({"OP", "ST", "TR"}),
    "RP": frozenset({"OP", "ST", "TR", "SV"}),
    "ADM": frozenset({"RP", "SV", "TR", "ST", "OP", "RKT", "SMM"}),
}

# ADM не видит и не меняет эти должности ни на каком проекте.
HIDDEN_FROM_ADM: frozenset[str] = frozenset({"ADM", "IT"})


def is_superuser(current_user: dict[str, Any]) -> bool:
    """Суперпользователь отличается правом физического удаления."""
    return current_user.get("id_role") == 2


def get_all_job_codes(cursor) -> set[str]:
    """Все коды должностей, которые есть в справочнике."""
    cursor.execute("SELECT code FROM JobTitle")
    return {row["code"] for row in cursor.fetchall()}


def get_project_ids(cursor, employee_id: int) -> set[str]:
    """Номера проектов, на которых сотрудник работает (как текст)."""
    cursor.execute(
        """
        SELECT id_project
        FROM Project_Employee
        WHERE id_emp = %s AND is_deleted = 0
        """,
        (employee_id,),
    )
    return {str(row["id_project"]) for row in cursor.fetchall()}


def get_target_info(cursor, employee_id: int) -> dict[str, Any] | None:
    """Собирает должности и проекты сотрудника, которого хотим посмотреть."""
    cursor.execute(
        """
        SELECT jt.code AS global_job
        FROM Employee e
        LEFT JOIN JobTitle jt ON jt.id = e.id_global_job
        WHERE e.id = %s
        """,
        (employee_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    cursor.execute(
        """
        SELECT jt.code AS code, pe.id_project AS project_id
        FROM Project_Employee pe
        JOIN JobTitle jt ON jt.id = pe.id_job
        WHERE pe.id_emp = %s AND pe.is_deleted = 0
        """,
        (employee_id,),
    )
    links = cursor.fetchall()

    job_codes = {link["code"] for link in links}
    if row["global_job"]:
        job_codes.add(row["global_job"])
    if not job_codes:
        job_codes = {"OP"}  # сотрудник без должности считается оператором

    return {
        "global_job": row["global_job"],
        "job_codes": job_codes,
        "projects": {str(link["project_id"]) for link in links},
    }


def can_view(
    cursor, current_user: dict[str, Any], target_employee_id: int
) -> bool:
    """Можно ли открыть карточку сотрудника."""
    my_job = current_user["effective_job_code"]
    if is_superuser(current_user) or my_job == "IT":
        return True

    target = get_target_info(cursor, target_employee_id)
    if target is None:
        return False

    if my_job == "ADM":
        return not (target["job_codes"] & HIDDEN_FROM_ADM)

    allowed = VIEW_LEVELS.get(my_job)
    if allowed is None or target["global_job"]:
        return False
    if not target["job_codes"].issubset(allowed):
        return False

    # Обязательное условие: общий проект с проверяющим.
    my_projects = get_project_ids(cursor, current_user["id"])
    return bool(my_projects & target["projects"])


def can_edit(
    cursor, current_user: dict[str, Any], target_employee_id: int
) -> bool:
    """Можно ли менять карточку сотрудника."""
    my_job = current_user["effective_job_code"]
    if is_superuser(current_user) or my_job == "IT":
        return True

    target = get_target_info(cursor, target_employee_id)
    if target is None:
        return False

    if my_job == "ADM":
        return not (target["job_codes"] & HIDDEN_FROM_ADM)

    allowed = EDIT_LEVELS.get(my_job)
    if allowed is None or target["global_job"]:
        return False
    if not target["job_codes"].issubset(allowed):
        return False

    my_projects = get_project_ids(cursor, current_user["id"])
    return bool(my_projects & target["projects"])


def allowed_job_codes(
    current_user: dict[str, Any], all_job_codes: set[str]
) -> set[str] | None:
    """Должности, которые пользователь может назначить.

    None означает «все должности», пустой набор — «никаких» (только чтение).
    """
    my_job = current_user["effective_job_code"]
    if is_superuser(current_user) or my_job == "IT":
        return None
    if my_job == "ADM":
        return all_job_codes - HIDDEN_FROM_ADM
    return set(ASSIGN_LEVELS.get(my_job, set()))


def get_allowed_projects(
    cursor, current_user: dict[str, Any]
) -> list[dict[str, Any]]:
    """Список проектов, с которыми пользователю разрешено работать."""
    my_job = current_user["effective_job_code"]

    if is_superuser(current_user) or my_job in ("IT", "ADM"):
        cursor.execute(
            "SELECT id, name FROM Project WHERE is_deleted = 0 ORDER BY name"
        )
        return cursor.fetchall()

    if my_job in ("SV", "RP"):
        # Свой проект — тот, где у человека есть его собственная должность.
        cursor.execute(
            """
            SELECT DISTINCT p.id, p.name
            FROM Project p
            JOIN Project_Employee pe ON pe.id_project = p.id
            JOIN JobTitle jt ON jt.id = pe.id_job
            WHERE pe.id_emp = %s AND pe.is_deleted = 0 AND p.is_deleted = 0
              AND jt.code = %s
            ORDER BY p.name
            """,
            (current_user["id"], my_job),
        )
        return cursor.fetchall()

    return []


def get_allowed_jobs(
    cursor, current_user: dict[str, Any]
) -> list[dict[str, Any]]:
    """Список должностей, которые пользователь может выбрать в карточке."""
    allowed_codes = allowed_job_codes(current_user, get_all_job_codes(cursor))

    if allowed_codes is None:
        cursor.execute("SELECT id, code, name FROM JobTitle ORDER BY id")
        return cursor.fetchall()
    if not allowed_codes:
        return []

    placeholders = ", ".join(["%s"] * len(allowed_codes))
    cursor.execute(
        "SELECT id, code, name FROM JobTitle "
        f"WHERE code IN ({placeholders}) ORDER BY id",
        tuple(sorted(allowed_codes)),
    )
    return cursor.fetchall()
