import os
import json
import pymysql

# ---------------------------------------------------------------
# Конфигурация подключения
# ---------------------------------------------------------------
DB_CONFIG = {
    'host':     os.getenv('DB_HOST', '192.168.10.220'),
    'port':     int(os.getenv('DB_PORT', '3308')),
    'user':     os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '2nsfa@423FdsC0fHvL'),
    'database': os.getenv('DB_NAME', 'MariaDB_to_test_Anton'),
    'charset':  'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': False,
}

def get_connection(include_db: bool = True):
    cfg = DB_CONFIG.copy()
    if not include_db:
        cfg.pop('database', None)
    return pymysql.connect(**cfg)

# ---------------------------------------------------------------
# DDL
# ---------------------------------------------------------------
DDL_STATEMENTS = [
    # 1. Справочник уровней доступа
    """
    CREATE TABLE IF NOT EXISTS AccessLevel (
        id          TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        code        VARCHAR(20)  NOT NULL UNIQUE,
        name        VARCHAR(50)  NOT NULL,
        level       TINYINT UNSIGNED NOT NULL,
        description VARCHAR(255) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 2. Роли (глобальные права: может ли удалять навсегда)
    """
    CREATE TABLE IF NOT EXISTS Role (
        id              TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        code            VARCHAR(32) NOT NULL UNIQUE,
        name            VARCHAR(64) NOT NULL,
        can_hard_delete TINYINT(1)  NOT NULL DEFAULT 0,
        description     VARCHAR(255) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 3. Статусы сотрудников
    """
    CREATE TABLE IF NOT EXISTS Status (
        id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        name        VARCHAR(100) NOT NULL UNIQUE,
        description VARCHAR(255) NULL,
        is_deleted  TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at  DATETIME     NULL,
        deleted_by  INT UNSIGNED NULL,
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by  INT UNSIGNED NULL,
        updated_at  DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by  INT UNSIGNED NULL,
        INDEX idx_status_active (is_deleted)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 4. Должности (теперь с привязкой к AccessLevel)
    """
    CREATE TABLE IF NOT EXISTS JobTitle (
        id              TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        code            VARCHAR(20) NOT NULL UNIQUE,
        name            VARCHAR(50) NOT NULL,
        id_access_level TINYINT UNSIGNED NOT NULL DEFAULT 1,
        is_global       TINYINT(1)  NOT NULL DEFAULT 0,
        CONSTRAINT fk_job_access FOREIGN KEY (id_access_level)
            REFERENCES AccessLevel(id) ON DELETE RESTRICT ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 5. Сотрудники (Employee вместо User)
    """
    CREATE TABLE IF NOT EXISTS Employee (
        id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        first_name      VARCHAR(100) NOT NULL,
        second_name     VARCHAR(100) NOT NULL,
        curname         VARCHAR(100) NULL,
        phone           VARCHAR(50)  NULL,
        birthday        DATE         NULL,
        login_ad        VARCHAR(100) NULL UNIQUE,
        password_ad     VARCHAR(255) NULL,
        email_telescope VARCHAR(255) NULL UNIQUE,
        email_cross     VARCHAR(255) NULL UNIQUE,
        password_email  VARCHAR(255) NULL,
        id_status       INT UNSIGNED NULL,
        id_role         TINYINT UNSIGNED NOT NULL DEFAULT 1,
        id_global_job   TINYINT UNSIGNED NULL,
        is_deleted      TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at      DATETIME     NULL,
        deleted_by      INT UNSIGNED NULL,
        created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by      INT UNSIGNED NULL,
        updated_at      DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by      INT UNSIGNED NULL,
        CONSTRAINT fk_emp_status FOREIGN KEY (id_status)
            REFERENCES Status(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        CONSTRAINT fk_emp_role FOREIGN KEY (id_role)
            REFERENCES Role(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        CONSTRAINT fk_emp_global_job FOREIGN KEY (id_global_job)
            REFERENCES JobTitle(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        INDEX idx_emp_active (is_deleted),
        INDEX idx_emp_status (id_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 6. Локации (Location вместо Place)
    """
    CREATE TABLE IF NOT EXISTS Location (
        id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        name       VARCHAR(255) NOT NULL UNIQUE,
        is_deleted TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at DATETIME     NULL,
        deleted_by INT UNSIGNED NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by INT UNSIGNED NULL,
        updated_at DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by INT UNSIGNED NULL,
        INDEX idx_loc_active (is_deleted)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 7. Проекты/Кампании (Project вместо Project)
    """
    CREATE TABLE IF NOT EXISTS Project (
        id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        name       VARCHAR(255) NOT NULL UNIQUE,
        is_deleted TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at DATETIME     NULL,
        deleted_by INT UNSIGNED NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by INT UNSIGNED NULL,
        updated_at DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by INT UNSIGNED NULL,
        INDEX idx_camp_active (is_deleted)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 8. Локации сотрудников
    """
    CREATE TABLE IF NOT EXISTS Location_Employee (
        id_loc     INT UNSIGNED NOT NULL,
        id_emp     INT UNSIGNED NOT NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by INT UNSIGNED NULL,
        is_deleted TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at DATETIME     NULL,
        deleted_by INT UNSIGNED NULL,
        PRIMARY KEY (id_loc, id_emp),
        CONSTRAINT fk_le_location FOREIGN KEY (id_loc)
            REFERENCES Location(id)  ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_le_employee FOREIGN KEY (id_emp)
            REFERENCES Employee(id)  ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 9. Сотрудники на проектах (БЕЗ id_access_level, он теперь в JobTitle)
    """
    CREATE TABLE IF NOT EXISTS Project_Employee (
        id_project     INT UNSIGNED NOT NULL,
        id_emp      INT UNSIGNED NOT NULL,
        id_job      TINYINT UNSIGNED NOT NULL,
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by  INT UNSIGNED NULL,
        updated_at  DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by  INT UNSIGNED NULL,
        is_deleted  TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at  DATETIME     NULL,
        deleted_by  INT UNSIGNED NULL,
        PRIMARY KEY (id_project, id_emp, id_job),
        CONSTRAINT fk_ce_Project FOREIGN KEY (id_project)
            REFERENCES Project(id) ON DELETE CASCADE  ON UPDATE CASCADE,
        CONSTRAINT fk_ce_employee FOREIGN KEY (id_emp)
            REFERENCES Employee(id) ON DELETE CASCADE  ON UPDATE CASCADE,
        CONSTRAINT fk_ce_job FOREIGN KEY (id_job)
            REFERENCES JobTitle(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        INDEX idx_ce_active (is_deleted)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 10. Аккаунты звонилки
    """
    CREATE TABLE IF NOT EXISTS TouchPoint (
        id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        id_project    INT UNSIGNED NULL,
        id_emp     INT UNSIGNED NULL,
        name       VARCHAR(255) NOT NULL,
        is_deleted TINYINT(1)   NOT NULL DEFAULT 0,
        deleted_at DATETIME     NULL,
        deleted_by INT UNSIGNED NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by INT UNSIGNED NULL,
        updated_at DATETIME     NULL ON UPDATE CURRENT_TIMESTAMP,
        updated_by INT UNSIGNED NULL,
        CONSTRAINT fk_tp_Project FOREIGN KEY (id_project)
            REFERENCES Project(id) ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_tp_employee FOREIGN KEY (id_emp)
            REFERENCES Employee(id) ON DELETE CASCADE ON UPDATE CASCADE,
        UNIQUE KEY uq_tp_camp_emp (id_project, id_emp),
        INDEX idx_tp_active (is_deleted)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,

    # 11. Журнал изменений
    """
    CREATE TABLE IF NOT EXISTS AuditLog (
        id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        table_name      VARCHAR(64)  NOT NULL,
        record_id       INT UNSIGNED NOT NULL,
        action          VARCHAR(20)  NOT NULL,
        id_emp          INT UNSIGNED NULL,
        emp_snapshot    VARCHAR(255) NULL,
        changed_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        changes         JSON         NULL,
        comment         TEXT         NULL,
        INDEX idx_audit_table_record (table_name, record_id),
        INDEX idx_audit_emp (id_emp),
        INDEX idx_audit_changed_at (changed_at),
        CONSTRAINT fk_audit_employee FOREIGN KEY (id_emp)
            REFERENCES Employee(id) ON DELETE SET NULL ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

SEED_STATEMENTS = [
    # Уровни доступа
    "INSERT IGNORE INTO AccessLevel (id, code, name, level, description) VALUES "
    "(1, 'CLOSED', 'Закрыт',            0, 'Нет доступа'), "
    "(2, 'VIEW',   'Просмотр',          1, 'Только чтение'), "
    "(3, 'EDIT',   'Редактирование',    2, 'Менять можно, удалять нельзя'), "
    "(4, 'FULL',   'Полный доступ',     3, 'Полный доступ в рамках проекта')",

    # Роли (1=User, 2=Superuser)
    "INSERT IGNORE INTO Role (id, code, name, can_hard_delete, description) VALUES "
    "(1, 'USER',      'Пользователь',      0, 'Обычный сотрудник'), "
    "(2, 'SUPERUSER', 'Суперпользователь', 1, 'Физическое удаление записей')",

    # Должности (теперь с привязкой к AccessLevel)
    "INSERT IGNORE INTO JobTitle (id, code, name, id_access_level, is_global) VALUES "
    "(1, 'IT',  'IT-специалист',           4, 1), "
    "(2, 'ADM', 'Администратор',           4, 1), "
    "(3, 'RP',  'РП (Project Manager)',    4, 0), "
    "(4, 'SV',  'СВ (Supervisor)',         3, 0), "
    "(5, 'TR',  'Тренер',                  3, 0), "
    "(6, 'ST',  'СТ (Старший специалист)', 3, 0), "
    "(7, 'OP',  'Оператор',                2, 0), "
    "(8, 'RKT', 'Рекрутер',                3, 0), "
    "(9, 'SMM', 'СММ',                     3, 0)"
]

def init_database():
    # 1) Сама БД
    conn = get_connection(include_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    # 2) Таблицы + справочники
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ddl in DDL_STATEMENTS:
                cur.execute(ddl)
            for seed in SEED_STATEMENTS:
                cur.execute(seed)
        conn.commit()
    finally:
        conn.close()

    print("ALL Correct!")

# ---------------------------------------------------------------
# Хелпер аудита
# ---------------------------------------------------------------
def log_change(conn, *, table_name, record_id, action, id_emp,
               changes=None, comment=None, emp_snapshot=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO AuditLog "
            "(table_name, record_id, action, id_emp, emp_snapshot, changes, comment) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                table_name, record_id, action, id_emp, emp_snapshot,
                json.dumps(changes, ensure_ascii=False) if changes is not None else None,
                comment,
            ),
        )

if __name__ == '__main__':
    init_database()