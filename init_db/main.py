"""Веб-часть системы учёта сотрудников.

Страницы, поиск, карточки и загрузка файлов находятся здесь. Подключение к базе
берётся из config.ini и db.py, права — из access.py. В коде нет паролей
и IP-адресов.
"""

import io
import json
import math
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from access import (
    VIEW_LEVELS,
    allowed_job_codes,
    can_edit as access_can_edit,
    can_restore,
    can_view as access_can_view,
    get_allowed_jobs,
    get_allowed_projects,
    get_all_job_codes,
)
from config_loader import SECTION_WEB, load_config
from db import get_connection
from init_db import log_change
from utils import (
    clean_birthday,
    clean_phone_number,
    generate_password,
    make_touchpoint_login,
    normalize_name,
    parse_fio_and_make_login,
    read_excel_quietly,
    sanitize_value,
    transliterate,
)

# Настройки читаются один раз при запуске сервера.
APP_CONFIG = load_config()
WEB_CONFIG = APP_CONFIG[SECTION_WEB]

# Все пути считаются от папки с main.py, а не от текущей папки консоли.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Worker Database System")
app.add_middleware(SessionMiddleware, secret_key=WEB_CONFIG["secret_key"])
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(CURRENT_DIR, "static")),
    name="static",
)

templates = Jinja2Templates(directory=os.path.join(CURRENT_DIR, "templates"))


def get_project_names() -> list[str]:
    """Берём список проектов из базы, чтобы он не расходился со справочником."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM Project WHERE is_deleted = 0 ORDER BY name"
            )
            return [row["name"] for row in cursor.fetchall()]
    finally:
        connection.close()

# ==========================================
# 2. АВТОРИЗАЦИЯ И СЕССИИ
# ==========================================

def get_current_user(request: Request):
    """Находит сотрудника по номеру в сессии и определяет его уровень доступа."""
    user_id = request.session.get("user_id")
    if not user_id: return None
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT e.*, r.can_hard_delete, jt.code as global_job_code
                FROM Employee e
                LEFT JOIN Role r ON e.id_role = r.id
                LEFT JOIN JobTitle jt ON e.id_global_job = jt.id
                WHERE e.id = %s AND e.is_deleted = 0
            """, (user_id,))
            user = cursor.fetchone()
            
            if user:
                if not user['global_job_code']:
                    cursor.execute("""
                        SELECT jt.code FROM Project_Employee pe
                        JOIN JobTitle jt ON pe.id_job = jt.id
                        WHERE pe.id_emp = %s AND pe.is_deleted = 0
                        ORDER BY jt.id_access_level DESC LIMIT 1
                    """, (user['id'],))
                    res = cursor.fetchone()
                    user['effective_job_code'] = res['code'] if res else 'OP'
                else:
                    user['effective_job_code'] = user['global_job_code']
            return user
    finally:
        conn.close()

def require_login(request: Request):
    """Пускает дальше только вошедших пользователей."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user

def get_all_job_titles():
    """Все должности из справочника для выпадающих списков."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, code, name FROM JobTitle ORDER BY id ASC")
            return cursor.fetchall()
    finally:
        conn.close()


def get_status_list():
    """Все статусы из справочника.

    В форме значение — id, а на экране показывается name из этой же строки.
    Так названия не расходятся с базой и не появляются лишние слова.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM Status ORDER BY id")
            return cursor.fetchall()
    finally:
        conn.close()


def get_job_id_by_code(cursor, code="OP"):
    """Возвращает номер должности по её короткому коду (например, OP)."""
    cursor.execute("SELECT id FROM JobTitle WHERE code = %s LIMIT 1", (code,))
    res = cursor.fetchone()
    return res['id'] if res else 7 

# ==========================================
# 3. ПРОВЕРКА ПРАВ
# ==========================================
# Сами правила живут в access.py. Здесь оставлены тонкие обёртки, чтобы
# остальной код не менялся и не дублировал иерархию.

def can_view_employee(cursor, current_user, target_emp_id) -> bool:
    """Можно ли открыть карточку сотрудника."""
    return access_can_view(cursor, current_user, target_emp_id)


def can_edit_employee(cursor, current_user, target_emp_id) -> bool:
    """Можно ли менять карточку сотрудника."""
    return access_can_edit(cursor, current_user, target_emp_id)


def require_editor(user, *, mass_operation: bool = False) -> None:
    """Пускает к добавлению/загрузке только тех, кому это разрешено.

    Массовые операции (загрузка файлов) доступны IT, администратору
    и суперпользователю. Обычное добавление — ещё и СВ/РП.
    """
    job_code = user["effective_job_code"]
    permitted = job_code in ("IT", "ADM") or user["id_role"] == 2
    if not mass_operation:
        permitted = permitted or job_code in ("SV", "RP")
    if not permitted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="У вас нет прав на это действие",
        )

# ==========================================
# 4. ХЕЛПЕРЫ ДЛЯ РАБОТЫ С ДАННЫМИ
# ==========================================

def get_or_create_location(cursor, place_name: str) -> int:
    """Находит локацию в справочнике или создаёт её, если её ещё нет."""
    if not place_name: return None
    cursor.execute("SELECT id FROM Location WHERE name = %s", (place_name,))
    res = cursor.fetchone()
    if res: return res['id']
    cursor.execute("INSERT INTO Location (name) VALUES (%s)", (place_name,))
    return cursor.lastrowid

def sync_employee_projects(cursor, person_id: int, projects: list, job_title_id: int, login_ad: str):
    """Привязывает нового сотрудника к проектам и создаёт входы в звонилку."""
    created_suffixes = set()
    for proj_item in projects:
        if not proj_item: continue
        
        if isinstance(proj_item, int) or str(proj_item).isdigit():
            proj_id = int(proj_item)
            cursor.execute("SELECT name FROM Project WHERE id = %s", (proj_id,))
            res = cursor.fetchone()
            if not res: continue
            proj_name = res['name']
        else:
            proj_name = str(proj_item)
            cursor.execute("SELECT id FROM Project WHERE name = %s", (proj_name,))
            res = cursor.fetchone()
            proj_id = res['id'] if res else None
            if not proj_id:
                cursor.execute("INSERT INTO Project (name) VALUES (%s)", (proj_name,))
                proj_id = cursor.lastrowid
            
        cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, %s)", (proj_id, person_id, job_title_id))
        
        p_upper = proj_name.upper()
        suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
        if suffix and suffix not in created_suffixes:
            tp_login = make_touchpoint_login(login_ad, proj_name)
            if tp_login:
                cursor.execute("INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) VALUES (%s, %s, %s)", (proj_id, person_id, tp_login))
            created_suffixes.add(suffix)

def generate_unique_login(cursor, fio_str: str) -> tuple:
    """Составляет логин из ФИО и добавляет цифру, если логин уже занят."""
    f_len, c_len = 1, 1
    digit_tail = ""
    while True:
        s_name, f_name, c_name, login_ad = parse_fio_and_make_login(fio_str, f_len=f_len, c_len=c_len)
        if digit_tail: login_ad = f"{login_ad}{digit_tail}"
        
        cursor.execute('SELECT id FROM Employee WHERE login_ad = %s OR email_telescope = %s OR email_cross = %s', 
                       (login_ad, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc"))
        if not cursor.fetchone(): 
            return s_name, f_name, c_name, login_ad
            
        max_f_len = len(transliterate(f_name))
        max_c_len = len(transliterate(c_name)) if c_name else 0
        
        if f_len < max_f_len: f_len += 1
        elif max_c_len and c_len < max_c_len: c_len += 1
        else: digit_tail = "1" if not digit_tail else str(int(digit_tail) + 1)

# ==========================================
# 5. АВТОРИЗАЦИЯ (РОУТЫ)
# ==========================================

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Показывает форму входа."""
    if request.session.get("user_id"): return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, login_ad: str = Form(...), password_ad: str = Form(...)):
    """Проверяет логин и пароль. Операторы на сайт не допускаются."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT e.id, jt.code as global_job_code 
                FROM Employee e
                LEFT JOIN JobTitle jt ON e.id_global_job = jt.id
                WHERE e.login_ad = %s AND e.password_ad = %s AND e.is_deleted = 0
            """, (login_ad, password_ad))
            user = cursor.fetchone()
            
            if user:
                job_code = user['global_job_code']
                if not job_code:
                    cursor.execute("""
                        SELECT jt.code FROM Project_Employee pe
                        JOIN JobTitle jt ON pe.id_job = jt.id
                        WHERE pe.id_emp = %s AND pe.is_deleted = 0
                        ORDER BY jt.id_access_level DESC LIMIT 1
                    """, (user['id'],))
                    res = cursor.fetchone()
                    job_code = res['code'] if res else 'OP'

                if job_code == 'OP':
                    return templates.TemplateResponse(request=request, name="login.html", 
                        context={"error": "Недостаточно прав для входа (Операторы не имеют доступа)."})
                
                request.session["user_id"] = user["id"]
                return RedirectResponse(url="/", status_code=303)
            else:
                return templates.TemplateResponse(request=request, name="login.html", context={"error": "Неверный логин или пароль"})
    finally:
        conn.close()

@app.get("/logout")
def logout(request: Request):
    """Завершает работу: очищает данные о вошедшем пользователе."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

# ==========================================
# 6. ЛОГИ
# ==========================================

@app.get("/logs", response_class=HTMLResponse)
def view_logs(request: Request, user=Depends(require_login),
              date_from: str = Query(None), date_to: str = Query(None),
              target_fio: str = Query(None), changer_fio: str = Query(None),
              project_filter: str = Query("all"), action_filter: str = Query("all"),
              page: int = Query(1)):
    """Показывает журнал изменений с фильтрами и постранично."""
    if user['effective_job_code'] not in ['IT', 'ADM'] and user['id_role'] != 2:
        return HTMLResponse("<h3>У вас нет прав на просмотр логов. Обратитесь к IT.</h3>", status_code=403)

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT a.id, a.record_id, a.changed_at, a.action, a.comment, a.changes,
                       t.id AS target_id,
                       CONCAT(t.second_name, ' ', t.first_name, ' ', IFNULL(t.curname, '')) as target_name,
                       CONCAT(c.second_name, ' ', c.first_name, ' ', IFNULL(c.curname, '')) as changer_name,
                       GROUP_CONCAT(DISTINCT p.name SEPARATOR ', ') as projects
                FROM AuditLog a
                LEFT JOIN Employee t ON a.record_id = t.id AND a.table_name = 'Employee'
                LEFT JOIN Employee c ON a.id_emp = c.id
                LEFT JOIN Project_Employee pe ON t.id = pe.id_emp
                LEFT JOIN Project p ON pe.id_project = p.id
                WHERE 1=1
            """
            conditions = []
            params = []

            if date_from:
                conditions.append("a.changed_at >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("a.changed_at <= %s")
                params.append(f"{date_to} 23:59:59")
            if target_fio:
                conditions.append("LOWER(CONCAT(t.second_name, ' ', t.first_name)) LIKE %s")
                params.append(f"%{target_fio.strip().lower()}%")
            if changer_fio:
                conditions.append("LOWER(CONCAT(c.second_name, ' ', c.first_name)) LIKE %s")
                params.append(f"%{changer_fio.strip().lower()}%")
            if project_filter and project_filter != 'all':
                conditions.append("p.name = %s")
                params.append(project_filter)
            if action_filter and action_filter != 'all':
                conditions.append("a.action = %s")
                params.append(action_filter)

            if conditions:
                sql += " AND " + " AND ".join(conditions)

            sql += " GROUP BY a.id ORDER BY a.changed_at DESC LIMIT 50 OFFSET %s"
            params.append((page - 1) * 50)

            cursor.execute(sql, tuple(params))
            logs = cursor.fetchall()

            # Названия статусов нужны только для показа. В базе по-прежнему
            # хранится id_status, поиск по названию нигде не выполняется.
            cursor.execute("SELECT id, name FROM Status")
            status_names = {row["id"]: row["name"] for row in cursor.fetchall()}

            def display_value(field: str, value: Any) -> Any:
                """Показывает статус названием, остальные поля — как есть."""
                if field != "Статус":
                    return value
                try:
                    return status_names.get(int(value), value)
                except (TypeError, ValueError):
                    return value

            for log in logs:
                log["changes_formatted"] = ""
                if not log["changes"]:
                    continue
                try:
                    changes_list = json.loads(log["changes"])
                except (TypeError, ValueError):
                    # Если в журнале лежит старый формат, покажем его как есть.
                    log["changes_formatted"] = log["changes"]
                    continue

                if isinstance(changes_list, list):
                    parts = []
                    for change in changes_list:
                        if not isinstance(change, dict):
                            continue
                        field = change.get("field", "")
                        old_value = display_value(field, change.get("old"))
                        new_value = display_value(field, change.get("new"))
                        parts.append(f"{field}: {old_value} ➔ {new_value}")
                    log["changes_formatted"] = ", ".join(parts)
                else:
                    log["changes_formatted"] = log["changes"]

    finally:
        conn.close()

    # Этот адрес вернёт человека в журнал с теми же фильтрами и страницей.
    return_url = "/logs?" + urlencode({
        "date_from": date_from or "",
        "date_to": date_to or "",
        "target_fio": target_fio or "",
        "changer_fio": changer_fio or "",
        "project_filter": project_filter or "all",
        "action_filter": action_filter or "all",
        "page": page,
    })

    return templates.TemplateResponse(request=request, name="logs.html", context={
        "current_user": user, "logs": logs,
        "date_from": date_from, "date_to": date_to,
        "target_fio": target_fio, "changer_fio": changer_fio,
        "project_filter": project_filter, "action_filter": action_filter,
        "project_list": get_project_names(), "page": page,
        "return_url": return_url
    })

# ==========================================
# 7. ПОИСК И ГЛАВНАЯ
# ==========================================

def search_employees_advanced(
    current_user,
    query: str,
    status_filter: str,
    place_filter: list[str],
    project_filter: str,
    date_from: str,
    date_to: str,
    page: int,
    limit_val: str,
    sort_dir: str = "asc",
    show_archived: bool = False,
):
    """Ищет сотрудников с учётом прав, фильтров и страницы.

    Возвращает (список людей, сколько всего нашлось, номер текущей страницы).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            sql_select = """
                SELECT DISTINCT e.id, e.first_name, e.second_name, e.curname, e.login_ad, e.password_ad, 
                       e.phone, e.birthday, s.name AS status_name, e.id_status, e.is_deleted,
                       GROUP_CONCAT(DISTINCT loc.name SEPARATOR ', ') as group_places,
                       GROUP_CONCAT(DISTINCT CONCAT(p.name, ' (', jt.code, ')') SEPARATOR ', ') as group_projects
                FROM Employee e
                LEFT JOIN Status s ON e.id_status = s.id
                LEFT JOIN Location_Employee le ON e.id = le.id_emp AND le.is_deleted = 0
                LEFT JOIN Location loc ON le.id_loc = loc.id AND loc.is_deleted = 0
                LEFT JOIN Project_Employee pe ON e.id = pe.id_emp AND pe.is_deleted = 0
                LEFT JOIN Project p ON pe.id_project = p.id AND p.is_deleted = 0
                LEFT JOIN JobTitle jt ON pe.id_job = jt.id
            """
            params: list[Any] = []
            my_job = current_user["effective_job_code"]
            my_id = current_user["id"]

            # Архив показываем только тем, кому это разрешено.
            if show_archived and (my_job in ("IT", "ADM") or current_user["id_role"] == 2):
                sql_select += " WHERE e.is_deleted = 1"
            else:
                sql_select += " WHERE e.is_deleted = 0"

            # Ограничение видимости. Правила берём из access.py, а здесь только
            # превращаем их в условие запроса к базе.
            if current_user["id_role"] != 2 and my_job != "IT":
                if my_job == "ADM":
                    # IS NULL обязателен: обычные сотрудники не имеют глобальной должности.
                    sql_select += """
                        AND (e.id_global_job IS NULL OR e.id_global_job NOT IN (
                            SELECT id FROM JobTitle WHERE code IN ('ADM', 'IT')
                        ))
                        AND NOT EXISTS (
                            SELECT 1 FROM Project_Employee pe_x
                            JOIN JobTitle jt_x ON jt_x.id = pe_x.id_job
                            WHERE pe_x.id_emp = e.id AND pe_x.is_deleted = 0
                              AND jt_x.code IN ('ADM', 'IT')
                        )
                    """
                elif my_job in VIEW_LEVELS:
                    allowed_codes = sorted(VIEW_LEVELS[my_job])
                    placeholders = ", ".join(["%s"] * len(allowed_codes))
                    sql_select += f"""
                        AND e.id_global_job IS NULL
                        AND EXISTS (
                            SELECT 1 FROM Project_Employee pe_my
                            JOIN Project_Employee pe_t ON pe_my.id_project = pe_t.id_project
                            JOIN JobTitle jt_t ON jt_t.id = pe_t.id_job
                            WHERE pe_my.id_emp = %s AND pe_my.is_deleted = 0
                              AND pe_t.id_emp = e.id AND pe_t.is_deleted = 0
                              AND jt_t.code IN ({placeholders})
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM Project_Employee pe_bad
                            JOIN JobTitle jt_bad ON jt_bad.id = pe_bad.id_job
                            WHERE pe_bad.id_emp = e.id AND pe_bad.is_deleted = 0
                              AND jt_bad.code NOT IN ({placeholders})
                        )
                    """
                    params.append(my_id)
                    params.extend(allowed_codes)
                    params.extend(allowed_codes)
                else:
                    return [], 0, 1

            conditions = []
            if place_filter:
                extended_places = list(place_filter)
                # Дом и УРМ в базе могут обозначать одно и то же место.
                if "Дом" in place_filter and "УРМ" not in extended_places:
                    extended_places.append("УРМ")
                if "УРМ" in place_filter and "Дом" not in extended_places:
                    extended_places.append("Дом")
                place_conds = " OR ".join(["loc_f.name = %s" for _ in extended_places])
                # EXISTS не сужает список проектов и локаций в строке результата.
                conditions.append(
                    "EXISTS (SELECT 1 FROM Location_Employee le_f "
                    "JOIN Location loc_f ON loc_f.id = le_f.id_loc "
                    f"WHERE le_f.id_emp = e.id AND le_f.is_deleted = 0 AND ({place_conds}))"
                )
                params.extend(extended_places)

            if project_filter and project_filter != "all":
                conditions.append(
                    "EXISTS (SELECT 1 FROM Project_Employee pe_f "
                    "JOIN Project p_f ON p_f.id = pe_f.id_project "
                    "WHERE pe_f.id_emp = e.id AND pe_f.is_deleted = 0 AND p_f.name = %s)"
                )
                params.append(project_filter)

            if query:
                query_clean = query.strip().lower()
                conditions.append(
                    "(LOWER(e.second_name) LIKE %s OR LOWER(e.first_name) LIKE %s "
                    "OR LOWER(IFNULL(e.curname, '')) LIKE %s OR LOWER(e.login_ad) LIKE %s)"
                )
                params.extend([f"%{query_clean}%"] * 4)
                
            if status_filter and status_filter != 'all':
                try:
                    status_id = int(status_filter)
                except (TypeError, ValueError):
                    status_id = None
                if status_id is not None:
                    conditions.append("e.id_status = %s")
                    params.append(status_id)
                
            if date_from:
                conditions.append("e.created_at >= %s")
                params.append(date_from)
            if date_to:
                conditions.append("e.created_at <= %s")
                params.append(f"{date_to} 23:59:59")
                
            if conditions:
                sql_select += " AND " + " AND ".join(conditions)
                
            count_query = f"SELECT COUNT(*) as cnt FROM ({sql_select} GROUP BY e.id) as subquery"
            cursor.execute(count_query, tuple(params))
            total_found = cursor.fetchone()["cnt"]

            # Если запрошена страница дальше последней, показываем последнюю.
            limit_number = int(limit_val) if limit_val and limit_val != "all" else 0
            total_pages = (
                math.ceil(total_found / limit_number)
                if limit_number > 0 and total_found > 0
                else 1
            )
            page = max(1, min(page, total_pages))

            direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
            sql_select += f" GROUP BY e.id ORDER BY e.second_name {direction}, e.first_name {direction}"

            if limit_number:
                sql_select += " LIMIT %s OFFSET %s"
                params.extend([limit_number, (page - 1) * limit_number])

            cursor.execute(sql_select, tuple(params))
            raw_persons = cursor.fetchall()

            persons = []
            for person in raw_persons:
                person["places"] = (
                    person["group_places"].split(", ") if person["group_places"] else []
                )
                person["projects"] = (
                    person["group_projects"].split(", ")
                    if person["group_projects"]
                    else []
                )
                person["can_edit"] = can_edit_employee(
                    cursor, current_user, person["id"]
                )
                persons.append(person)

            return persons, total_found, page
    finally:
        conn.close()

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(require_login)):
    """Главная страница: пустая таблица с фильтрами."""
    conn = get_connection()
    total_count = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM Employee WHERE is_deleted = 0")
            total_count = cursor.fetchone()['cnt']
    finally:
        conn.close()
        
    return templates.TemplateResponse(request=request, name="index.html", context={
        "total_count": total_count, "results": [], "search_query": "",
        "status_filter": "all", "place_filter": [], "project_filter": "all",
        "date_from": "", "date_to": "", "sort_dir": "asc",
        "current_page": 1, "limit": "50", "total_pages": 1,
        "project_list": get_project_names(),
        "job_titles": get_all_job_titles(), "current_user": user,
        "show_archived": False, "status_list": get_status_list()
    })

@app.get("/search", response_class=HTMLResponse)
def search(request: Request, user=Depends(require_login), 
           query: str = Query(None), status_filter: str = Query("all"), 
           place_filter: list[str] = Query([]), project_filter: str = Query("all"), 
           date_from: str = Query(None), date_to: str = Query(None), 
           page: int = Query(1), limit: str = Query("50"), sort_dir: str = Query("asc"),
           show_archived: str = Query("false")):
    """Отдаёт найденных сотрудников и номера страниц для таблицы."""
    search_query_clean = query.strip() if query else ""
    is_archived_view = (show_archived == "true")
    
    results, total_found, page = search_employees_advanced(
        user, search_query_clean, status_filter, place_filter, project_filter,
        date_from, date_to, page, limit, sort_dir, is_archived_view
    )

    limit_numeric = int(limit) if limit != "all" else 0
    total_pages = (
        math.ceil(total_found / limit_numeric)
        if limit_numeric > 0 and total_found > 0
        else 1
    )

    conn = get_connection()
    total_count = 0
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM Employee WHERE is_deleted = 0")
            total_count = cursor.fetchone()['cnt']
    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="index.html", context={
        "total_count": total_count, "results": results, "search_query": search_query_clean, 
        "status_filter": status_filter, "place_filter": place_filter, "project_filter": project_filter, 
        "date_from": date_from, "date_to": date_to, 
        "current_page": page, "limit": limit, "total_pages": total_pages, "sort_dir": sort_dir,
        "project_list": get_project_names(), "job_titles": get_all_job_titles(), "current_user": user,
        "show_archived": is_archived_view, "status_list": get_status_list()
    })

# ==========================================
# 8. КАРТОЧКА, ДОБАВЛЕНИЕ И РЕДАКТИРОВАНИЕ
# ==========================================

@app.get("/employee/{emp_id}")
def get_employee_details(emp_id: int, user=Depends(require_login)):
    """Отдаёт карточку сотрудника, если его можно посмотреть."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if not can_view_employee(cursor, user, emp_id):
                return {"error": "У вас нет прав на просмотр этой карточки."}

            cursor.execute("SELECT e.*, s.name as status_name FROM Employee e LEFT JOIN Status s ON e.id_status = s.id WHERE e.id = %s", (emp_id,))
            person = cursor.fetchone()
            if not person: return {"error": "Employee not found"}
            
            cursor.execute("SELECT l.name FROM Location l JOIN Location_Employee le ON l.id = le.id_loc WHERE le.id_emp = %s AND le.is_deleted = 0", (emp_id,))
            person['places'] = [r['name'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT p.name FROM Project p JOIN Project_Employee pe ON p.id = pe.id_project WHERE pe.id_emp = %s AND pe.is_deleted = 0", (emp_id,))
            person['projects'] = [r['name'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT name FROM TouchPoint WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['touchpoints'] = [r['name'] for r in cursor.fetchall()]
            
            person['can_edit'] = can_edit_employee(cursor, user, emp_id)
            person['can_restore'] = can_restore(user)
            person['can_hard_delete'] = bool(user['can_hard_delete'])
            return person
    finally:
        conn.close()

@app.post("/add_user")
def add_user(
    request: Request, user=Depends(require_login),
    fio: str = Form(...), phone: str = Form(None), birthday: str = Form(None),
    places: list[str] = Form([]), projects: list[str] = Form([]),
    job_title_id: int = Form(None), current_search: str = Form(""), is_lookalike: str = Form("false")
):
    """Добавляет одного сотрудника. Старший и тренер сюда не допускаются."""
    require_editor(user)
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cleaned_phone = clean_phone_number(phone)
            formatted_birthday = clean_birthday(birthday)

            second_name, first_name, curname, login_ad = generate_unique_login(cursor, fio)
            shared_password = generate_password()

            if job_title_id is None:
                job_title_id = get_job_id_by_code(cursor, "OP")

            # Не даём назначить должность выше своей.
            permitted_codes = allowed_job_codes(user, get_all_job_codes(cursor))
            cursor.execute("SELECT code FROM JobTitle WHERE id = %s", (job_title_id,))
            job_row = cursor.fetchone()
            if permitted_codes is not None and (
                job_row is None or job_row["code"] not in permitted_codes
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Нельзя назначить такую должность",
                )

            cursor.execute('''
                INSERT INTO Employee (first_name, second_name, curname, phone, birthday, login_ad, password_ad, email_telescope, email_cross, password_email, id_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
            ''', (first_name, second_name, curname, cleaned_phone, formatted_birthday, login_ad, shared_password, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc", shared_password))
            
            person_id = cursor.lastrowid
            log_change(conn, table_name='Employee', record_id=person_id, action='INSERT', id_emp=user['id'], comment="Создание сотрудника", changes=[])
            
            for place_name in places:
                loc_id = get_or_create_location(cursor, place_name)
                if loc_id:
                    cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, person_id))
            
            sync_employee_projects(cursor, person_id, projects, job_title_id, login_ad)
                    
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка базы данных: {e}</h3>", status_code=400)
    finally:
        conn.close()

    all_jobs = get_all_job_titles()
    selected_job = next((j for j in all_jobs if j['id'] == job_title_id), None)
    job_name = f"{selected_job['name']} ({selected_job['code']})" if selected_job else "OP (Оператор)"

    return templates.TemplateResponse(request=request, name="add_report.html", context={
        "second_name": second_name, "first_name": first_name, "curname": curname,
        "places": places, "projects": projects, "login_ad": login_ad, "password": shared_password,
        "job_name": job_name, "current_user": user
    })

@app.get("/employee/{emp_id}/edit_data")
def get_edit_data(emp_id: int, user=Depends(require_login)):
    """Отдаёт данные для окна редактирования и списки доступных вариантов."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if not can_edit_employee(cursor, user, emp_id):
                return {"error": "У вас нет прав на редактирование данных."}

            # Архивную карточку тоже разрешаем открыть для правки:
            # восстановление и изменение данных — разные действия.
            cursor.execute(
                """
                SELECT id, second_name, first_name, curname, phone, id_status
                FROM Employee
                WHERE id = %s
                """,
                (emp_id,),
            )
            person = cursor.fetchone()
            if not person:
                return {"error": "Сотрудник не найден"}

            cursor.execute(
                """
                SELECT id_project, id_job
                FROM Project_Employee
                WHERE id_emp = %s AND is_deleted = 0
                """,
                (emp_id,),
            )
            person["project_jobs"] = {
                row["id_project"]: row["id_job"] for row in cursor.fetchall()
            }

            cursor.execute(
                """
                SELECT id_loc
                FROM Location_Employee
                WHERE id_emp = %s AND is_deleted = 0
                """,
                (emp_id,),
            )
            person["selected_places"] = [row["id_loc"] for row in cursor.fetchall()]

            # Списки проектов и должностей тоже определяются в access.py.
            allowed_projects = get_allowed_projects(cursor, user)
            allowed_jobs = get_allowed_jobs(cursor, user)
            if not allowed_projects or not allowed_jobs:
                return {"error": "У вас нет прав на редактирование проектов."}

            return {
                "person": person,
                "all_projects": allowed_projects,
                "all_jobs": allowed_jobs,
            }
    finally:
        conn.close()

@app.post("/employee/{emp_id}/update")
async def update_employee(
    request: Request, emp_id: int, user=Depends(require_login),
    status_id: int = Form(...), phone: str = Form(None),
    places: list[str] = Form([]), current_search: str = Form(""),
    return_to: str = Form(""),
):
    """Сохраняет карточку и записывает разницу в журнал изменений.

    Связи с проектами и локациями не удаляются навсегда: старые строки только
    помечаются недействительными. Так история остается в базе.
    """
    form_data = await request.form()
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if not can_edit_employee(cursor, user, emp_id):
                return HTMLResponse(
                    "<script>alert('У вас нет прав на редактирование.'); "
                    "window.history.back();</script>"
                )

            cleaned_phone = clean_phone_number(phone)

            cursor.execute(
                "SELECT phone, id_status, login_ad FROM Employee WHERE id = %s",
                (emp_id,),
            )
            old_data = cursor.fetchone()
            if old_data is None:
                return HTMLResponse("<h3>Сотрудник не найден</h3>", status_code=404)

            # Статус проверяем по id. Название нужно только для показа,
            # поэтому поиска по словам вроде «уволен» здесь нет.
            cursor.execute("SELECT id FROM Status WHERE id = %s", (status_id,))
            if cursor.fetchone() is None:
                return HTMLResponse(
                    "<h3>Ошибка обновления: выбран неизвестный статус.</h3>",
                    status_code=400,
                )

            # Запоминаем, что было до изменений — это попадёт в журнал.
            cursor.execute(
                """
                SELECT l.name
                FROM Location_Employee le
                JOIN Location l ON l.id = le.id_loc
                WHERE le.id_emp = %s AND le.is_deleted = 0
                ORDER BY l.name
                """,
                (emp_id,),
            )
            old_places = sorted(row["name"] for row in cursor.fetchall())

            cursor.execute(
                """
                SELECT p.name, jt.code
                FROM Project_Employee pe
                JOIN Project p ON p.id = pe.id_project
                JOIN JobTitle jt ON jt.id = pe.id_job
                WHERE pe.id_emp = %s AND pe.is_deleted = 0
                """,
                (emp_id,),
            )
            old_projects = [
                f"{row['name']} ({row['code']})" for row in cursor.fetchall()
            ]

            permitted_projects = {
                str(row["id"]) for row in get_allowed_projects(cursor, user)
            }
            permitted_jobs = allowed_job_codes(user, get_all_job_codes(cursor))

            cursor.execute(
                """
                UPDATE Employee
                SET id_status = %s, phone = %s, updated_at = NOW(), updated_by = %s
                WHERE id = %s
                """,
                (status_id, cleaned_phone, user["id"], emp_id),
            )

            # Локации: старые связи гасим, новые включаем заново.
            cursor.execute(
                """
                UPDATE Location_Employee
                SET is_deleted = 1, deleted_at = NOW(), deleted_by = %s
                WHERE id_emp = %s AND is_deleted = 0
                """,
                (user["id"], emp_id),
            )
            new_places = []
            for place_name in places:
                location_id = get_or_create_location(cursor, place_name)
                if not location_id:
                    continue
                cursor.execute(
                    """
                    INSERT INTO Location_Employee (id_loc, id_emp, is_deleted)
                    VALUES (%s, %s, 0)
                    ON DUPLICATE KEY UPDATE is_deleted = 0,
                                           deleted_at = NULL,
                                           deleted_by = NULL
                    """,
                    (location_id, emp_id),
                )
                new_places.append(place_name)

            # Проекты и точки входа тоже помечаем недействительными, не удаляем.
            cursor.execute(
                """
                UPDATE Project_Employee
                SET is_deleted = 1, deleted_at = NOW(), deleted_by = %s
                WHERE id_emp = %s AND is_deleted = 0
                """,
                (user["id"], emp_id),
            )
            cursor.execute(
                """
                UPDATE TouchPoint
                SET is_deleted = 1, deleted_at = NOW(), deleted_by = %s
                WHERE id_emp = %s AND is_deleted = 0
                """,
                (user["id"], emp_id),
            )

            new_projects: list[str] = []
            if old_data["login_ad"]:
                for project_id in form_data.getlist("selected_projects"):
                    if str(project_id) not in permitted_projects:
                        continue

                    job_id = form_data.get(f"job_for_project_{project_id}")
                    if not job_id:
                        continue

                    cursor.execute(
                        "SELECT code FROM JobTitle WHERE id = %s", (job_id,)
                    )
                    job_row = cursor.fetchone()
                    if job_row is None:
                        continue
                    if permitted_jobs is not None and job_row["code"] not in permitted_jobs:
                        continue

                    cursor.execute(
                        "SELECT name FROM Project WHERE id = %s", (project_id,)
                    )
                    project_row = cursor.fetchone()
                    if project_row is None:
                        continue
                    project_name = project_row["name"]

                    cursor.execute(
                        """
                        INSERT INTO Project_Employee
                            (id_project, id_emp, id_job, is_deleted)
                        VALUES (%s, %s, %s, 0)
                        ON DUPLICATE KEY UPDATE is_deleted = 0,
                                               deleted_at = NULL,
                                               deleted_by = NULL,
                                               updated_by = %s
                        """,
                        (project_id, emp_id, job_id, user["id"]),
                    )

                    touchpoint_login = make_touchpoint_login(
                        old_data["login_ad"], project_name
                    )
                    if touchpoint_login:
                        cursor.execute(
                            """
                            INSERT INTO TouchPoint
                                (id_project, id_emp, name, is_deleted)
                            VALUES (%s, %s, %s, 0)
                            ON DUPLICATE KEY UPDATE is_deleted = 0,
                                                   deleted_at = NULL,
                                                   deleted_by = NULL
                            """,
                            (project_id, emp_id, touchpoint_login),
                        )

                    new_projects.append(f"{project_name} ({job_row['code']})")

            changes: list[dict[str, Any]] = []
            if old_data["phone"] != cleaned_phone:
                changes.append(
                    {"field": "Телефон", "old": old_data["phone"], "new": cleaned_phone}
                )
            if old_data["id_status"] != status_id:
                changes.append(
                    {"field": "Статус", "old": old_data["id_status"], "new": status_id}
                )

            old_projects_text = ", ".join(sorted(old_projects)) or "Нет"
            new_projects_text = ", ".join(sorted(new_projects)) or "Нет"
            if old_projects_text != new_projects_text:
                changes.append(
                    {
                        "field": "Проекты и должности",
                        "old": old_projects_text,
                        "new": new_projects_text,
                    }
                )

            old_places_text = ", ".join(old_places) or "Нет"
            new_places_text = ", ".join(sorted(set(new_places))) or "Нет"
            if old_places_text != new_places_text:
                changes.append(
                    {
                        "field": "Локации",
                        "old": old_places_text,
                        "new": new_places_text,
                    }
                )

            if changes:
                log_change(
                    conn,
                    table_name="Employee",
                    record_id=emp_id,
                    action="UPDATE",
                    id_emp=user["id"],
                    changes=changes,
                    comment="Ручное редактирование карточки",
                )

        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as error:
        conn.rollback()
        return HTMLResponse(
            content=f"<h3>Ошибка обновления: {error}</h3>", status_code=400
        )
    finally:
        conn.close()

    # Если карточку открыли из журнала, возвращаем туда с теми же фильтрами.
    if return_to.startswith("/logs"):
        return RedirectResponse(url=return_to, status_code=303)
    if current_search:
        return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.delete("/employee/{emp_id}")
def delete_employee(emp_id: int, user=Depends(require_login)):
    """Отправляет сотрудника в архив, не удаляя данные навсегда."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if not can_edit_employee(cursor, user, emp_id):
                return {"status": "error", "error": "У вас нет прав на архивацию"}

            cursor.execute("UPDATE Employee SET is_deleted = 1, deleted_at = NOW(), deleted_by = %s WHERE id = %s", (user['id'], emp_id))
            log_change(conn, table_name='Employee', record_id=emp_id, action='SOFT_DELETE', id_emp=user['id'], 
                       changes=[{"field": "is_deleted", "old": 0, "new": 1}], comment="Пометка об удалении")
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()

@app.post("/employee/{emp_id}/restore")
def restore_employee(emp_id: int, user=Depends(require_login)):
    """Возвращает сотрудника из архива.

    Доступно IT, администратору и суперпользователю.
    """
    if not can_restore(user):
        return {"status": "error", "error": "У вас нет прав на восстановление"}
        
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE Employee SET is_deleted = 0, deleted_at = NULL, deleted_by = NULL WHERE id = %s", (emp_id,))
            log_change(conn, table_name='Employee', record_id=emp_id, action='RESTORE', id_emp=user['id'], 
                       changes=[{"field": "is_deleted", "old": 1, "new": 0}], comment="Восстановление из архива")
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()

@app.delete("/employee/{emp_id}/hard_delete")
def hard_delete_employee(emp_id: int, user=Depends(require_login)):
    """Физически удаляет запись. Доступно только суперпользователю.

    Перед удалением обязательно сохраняем, кто и что удалил, в журнал.
    """
    if not user["can_hard_delete"]:
        return {"status": "error", "error": "У вас нет прав на физическое удаление"}

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT CONCAT(second_name, ' ', first_name) AS full_name
                FROM Employee
                WHERE id = %s
                """,
                (emp_id,),
            )
            employee = cursor.fetchone()
            if employee is None:
                return {"status": "error", "error": "Сотрудник не найден"}

            log_change(
                conn,
                table_name="Employee",
                record_id=emp_id,
                action="HARD_DELETE",
                id_emp=user["id"],
                changes=[
                    {
                        "field": "Запись",
                        "old": employee["full_name"],
                        "new": "Удалена навсегда",
                    }
                ],
                comment="Физическое удаление записи",
            )
            cursor.execute("DELETE FROM Employee WHERE id = %s", (emp_id,))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()

@app.get("/check_duplicate")
def check_duplicate(fio: str, user=Depends(require_login)):
    """Ищет в базе человека с таким же ФИО, чтобы не завести дубликат."""
    try:
        parts = fio.split()
        if len(parts) < 2: return {"exists": False}
            
        second_name = normalize_name(parts[0])
        first_name = normalize_name(parts[1])
        curname = normalize_name(parts[2]) if len(parts) > 2 else ""
        
        conn = get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    SELECT id, second_name, first_name, curname, phone, birthday FROM Employee 
                    WHERE second_name = %s AND first_name = %s AND curname = %s AND is_deleted = 0
                ''', (second_name, first_name, curname))
                
                person = cursor.fetchone()
                if not person: return {"exists": False}
                
                cursor.execute('''
                    SELECT p.name FROM Project p 
                    JOIN Project_Employee pe ON p.id = pe.id_project 
                    WHERE pe.id_emp = %s AND pe.is_deleted = 0
                ''', (person['id'],))
                projects = [r['name'] for r in cursor.fetchall()]
                
                proj_str = ", ".join(projects) if projects else "Не привязан к проектам"
                details = (
                    f"ФИО: {person['second_name']} {person['first_name']} {person['curname']}\n"
                    f"Дата рождения: {person['birthday'] or '—'}\n"
                    f"Телефон: {person['phone'] or '—'}\n"
                    f"Проекты: {proj_str}"
                )
                return {"exists": True, "details": details}
        finally:
            conn.close()
    except Exception as e: 
        return {"exists": False, "error": str(e)}

# ==========================================
# 9. ИМПОРТ И ОБОГАЩЕНИЕ
# ==========================================

@app.post("/import_file")
async def import_file(
    request: Request, user=Depends(require_login),
    file: UploadFile = File(...), default_project: str = Form(None),
    default_places: list[str] = Form([])
):
    """Читает загруженный файл и показывает, кого из строк мы добавим."""
    require_editor(user, mass_operation=True)
    contents = await file.read()
    filename = file.filename.lower()
    parsed_rows = []
    
    if filename.endswith('.txt'):
        try: text = contents.decode('utf-8')
        except UnicodeDecodeError: text = contents.decode('cp1251')
            
        for line in text.splitlines():
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) < 2: continue
            
            parsed_rows.append({
                "second_name": normalize_name(parts[0]),
                "first_name": normalize_name(parts[1]),
                "curname": normalize_name(parts[2]) if len(parts) > 2 else "",
                "phone": "", "birthday": None,
                "projects": [default_project] if default_project else [],
                "places": default_places
            })

    elif filename.endswith('.xlsx'):
        df = read_excel_quietly(io.BytesIO(contents), dtype=str)
        df.columns = [str(c).strip().lower() for c in df.columns]
        has_fio = "фио" in df.columns
        has_split_name = "фамилия" in df.columns and "имя" in df.columns
        
        if not (has_fio or has_split_name):
            return HTMLResponse(content="<h3>Ошибка импорта: Нужны колонки 'ФИО' или 'Фамилия' и 'Имя'</h3>", status_code=400)
            
        birthday_col = next((c for c in ['день рождения', 'дата рождения', 'др', 'рождение', 'birthday'] if c in df.columns), None)

        for _, row in df.iterrows():
            if has_split_name:
                s_name = normalize_name(row.get('фамилия'))
                f_name = normalize_name(row.get('имя'))
                c_name = normalize_name(row.get('отчество'))
            else:
                fio_str = sanitize_value(row.get('фио'))
                fio_parts = fio_str.split()
                if len(fio_parts) < 2: continue
                s_name = normalize_name(fio_parts[0])
                f_name = normalize_name(fio_parts[1])
                c_name = normalize_name(fio_parts[2]) if len(fio_parts) > 2 else ""

            if not s_name or not f_name: continue

            phone_val = clean_phone_number(row.get('телефон', row.get('phone')))
            birthday_val = clean_birthday(row.get(birthday_col)) if birthday_col else None
            
            proj_val = sanitize_value(row.get('проект', row.get('project')))
            place_val = sanitize_value(row.get('локация', row.get('location')))
            
            row_projects = [proj_val] if proj_val else ([default_project] if default_project else [])
            row_places = ['Дом'] if place_val.upper() in ['УРМ', 'ДОМ'] else (['Офис'] if place_val.upper() == 'ОФИС' else ([place_val] if place_val else default_places))

            parsed_rows.append({
                "second_name": s_name, "first_name": f_name, "curname": c_name, 
                "phone": phone_val, "birthday": birthday_val, 
                "projects": row_projects, "places": row_places
            })
    else:
        return HTMLResponse(content="<h3>Ошибка: Неподдерживаемый формат файла</h3>", status_code=400)

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for person in parsed_rows:
                cursor.execute('''
                    SELECT id, second_name, first_name, curname, phone, birthday 
                    FROM Employee WHERE second_name = %s AND first_name = %s AND is_deleted = 0
                ''', (person['second_name'], person['first_name']))
                
                matches = cursor.fetchall()
                person['is_duplicate'] = False
                person['db_match_details'] = ""
                
                for m in matches:
                    db_curname = m['curname'] or ""
                    file_curname = person['curname'] or ""
                    
                    if db_curname and file_curname and db_curname != file_curname:
                        continue 
                    elif db_curname == file_curname:
                        person['is_duplicate'] = True
                        person['db_match_details'] = f"Полный тёзка в БД: {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                        break 
                    elif not db_curname or not file_curname:
                        person['is_duplicate'] = True
                        person['db_match_details'] = f"Подозрение на дубликат (нет отчества): {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                        break 
    finally:
        conn.close()
    
    return templates.TemplateResponse(request=request, name="import_preview.html", context={"rows": parsed_rows, "total_count": len(parsed_rows), "current_user": user})

@app.post("/confirm_import", response_class=HTMLResponse)
async def confirm_import(
    request: Request, user=Depends(require_login),
    import_indexes: list[int] = Form([]),
):
    """Добавляет в базу выбранные строки из файла."""
    require_editor(user, mass_operation=True)
    form_data = await request.form()
    success_reports = []
    ignored_count = 0
    
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            total_rows = sum(1 for key in form_data.keys() if key.startswith('second_name_'))
            default_op_id = get_job_id_by_code(cursor, "OP")
            
            for idx in range(total_rows):
                if idx not in import_indexes:
                    ignored_count += 1
                    continue
                    
                second_name = sanitize_value(form_data.get(f"second_name_{idx}"))
                first_name = sanitize_value(form_data.get(f"first_name_{idx}"))
                curname = sanitize_value(form_data.get(f"curname_{idx}"))
                phone = clean_phone_number(form_data.get(f"phone_{idx}"))
                birthday = clean_birthday(form_data.get(f"birthday_{idx}"))
                
                projects = form_data.getlist(f"projects_{idx}")
                places = form_data.getlist(f"places{idx}")
                
                fio_str = f"{second_name} {first_name} {curname}"
                _, _, _, login_ad = generate_unique_login(cursor, fio_str)
                shared_password = generate_password()

                cursor.execute('''
                    INSERT INTO Employee (first_name, second_name, curname, phone, birthday, login_ad, password_ad, email_telescope, email_cross, password_email, id_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                ''', (first_name, second_name, curname, phone, birthday, login_ad, shared_password, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc", shared_password))
                
                person_id = cursor.lastrowid
                log_change(conn, table_name='Employee', record_id=person_id, action='INSERT', id_emp=user['id'], comment="Импорт сотрудника из файла")
                
                for place_name in places:
                    loc_id = get_or_create_location(cursor, place_name)
                    if loc_id:
                        cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, person_id))
                    
                sync_employee_projects(cursor, person_id, projects, default_op_id, login_ad)
                        
                success_reports.append({
                    "fio": f"{second_name} {first_name} {curname}".strip(), 
                    "login": login_ad, "password": shared_password,
                    "projects_list": projects, "job_name": "OP (Оператор)"
                })
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка импорта: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    return templates.TemplateResponse(request=request, name="import_report.html", context={
        "success_list": success_reports, "success_count": len(success_reports), 
        "ignored_count": ignored_count, "current_user": user
    })

@app.post("/enrich_file")
async def enrich_file(request: Request, user=Depends(require_login), file: UploadFile = File(...)):
    """Ищет в файле телефоны и даты рождения и показывает, что можно дополнить."""
    require_editor(user, mass_operation=True)
    contents = await file.read()
    df = read_excel_quietly(io.BytesIO(contents), dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    has_fio = "фио" in df.columns
    has_split_name = "фамилия" in df.columns
    if not (has_fio or has_split_name):
        return HTMLResponse(content="<h3>Ошибка: В файле должны быть колонки 'ФИО' или 'Фамилия'</h3>", status_code=400)
        
    birthday_col = next((c for c in ['день рождения', 'дата рождения', 'др', 'рождение', 'birthday'] if c in df.columns), None)
    enrich_rows = []
    conn = get_connection()
    
    try:
        with conn.cursor() as cursor:
            for _, row in df.iterrows():
                if has_split_name:
                    s_name = normalize_name(row.get('фамилия'))
                    f_name = normalize_name(row.get('имя'))
                    c_name = normalize_name(row.get('отчество'))
                else:
                    fio_str = sanitize_value(row.get('фио'))
                    fio_parts = fio_str.split()
                    if len(fio_parts) < 1: continue
                    s_name = normalize_name(fio_parts[0])
                    f_name = normalize_name(fio_parts[1]) if len(fio_parts) > 1 else ""
                    c_name = normalize_name(fio_parts[2]) if len(fio_parts) > 2 else ""
        
                if not s_name: continue
                
                new_phone = clean_phone_number(row.get('телефон', row.get('phone')))
                new_birthday = clean_birthday(row.get(birthday_col)) if birthday_col else None
        
                if not new_phone and not new_birthday: continue
        
                if f_name:
                    cursor.execute("SELECT id, first_name, second_name, curname, phone, birthday FROM Employee WHERE second_name = %s AND first_name = %s AND is_deleted = 0", (s_name, f_name))
                else:
                    cursor.execute("SELECT id, first_name, second_name, curname, phone, birthday FROM Employee WHERE second_name = %s AND is_deleted = 0", (s_name,))
                    
                db_matches = cursor.fetchall()
                for db_user in db_matches:
                    db_id = db_user['id']
                    db_first_name = db_user['first_name'] or ""
                    db_curname = db_user['curname'] or ""
                    db_phone = db_user['phone'] or ""
                    db_birthday_raw = db_user['birthday'] or ""
                    
                    db_birthday = db_birthday_raw.strftime('%Y-%m-%d') if hasattr(db_birthday_raw, 'strftime') else str(db_birthday_raw)
                    
                    status_type, info_msg, auto_check = "normal", "", "checked"
        
                    if not f_name and db_first_name:
                        status_type, info_msg = "partial_mismatch", f"❓ Похожая запись по Фамилии (В базе: {db_first_name} {db_curname}, имя не указано)"
                        auto_check = ""
                    elif db_curname and c_name and db_curname != c_name:
                        status_type, info_msg = "partial_mismatch", f"❓ Похожая запись, но разные отчества (В базе: {db_curname}, в файле: {c_name})"
                        auto_check = ""
                    elif (not db_curname and c_name) or (db_curname and not c_name):
                        status_type, info_msg = "partial_empty", f"❓ Похожая запись (В базе: {db_curname or 'нет отчества'}, в файле: {c_name or 'нет отчества'})"
                        auto_check = ""
                    else:
                        is_phone_changed = new_phone and db_phone and db_phone != new_phone
                        is_birthday_changed = new_birthday and db_birthday and db_birthday != new_birthday
                        if is_phone_changed or is_birthday_changed:
                            status_type, info_msg = "conflict", "⚠️ Различаются телефон или дата рождения"
                        else:
                            if (new_phone == db_phone or not new_phone) and (new_birthday == db_birthday or not new_birthday): 
                                continue
                            status_type, info_msg = "auto", "✅ Данные будут дополнены"
        
                    enrich_rows.append({
                        "db_id": db_id, 
                        "fio": f"{s_name} {db_first_name} {db_curname}".strip(),
                        "file_fio": f"{s_name} {f_name or '[нет имени]'} {c_name}".strip(),
                        "db_phone": db_phone, 
                        "new_phone": new_phone if new_phone else db_phone,
                        "db_birthday": db_birthday, 
                        "new_birthday": new_birthday if new_birthday else db_birthday,
                        "status_type": status_type, 
                        "info_msg": info_msg, 
                        "auto_check": auto_check
                    })
    finally:
        conn.close()
    return templates.TemplateResponse(request=request, name="enrich_preview.html", context={"rows": enrich_rows, "total_count": len(enrich_rows), "current_user": user})

@app.post("/confirm_enrich", response_class=HTMLResponse)
async def confirm_enrich(
    request: Request, user=Depends(require_login),
    enrich_indexes: list[int] = Form([]),
):
    """Сохраняет выбранные телефоны и даты рождения и пишет отчёт."""
    require_editor(user, mass_operation=True)
    form_data = await request.form()
    report_list = []
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            total_rows = sum(1 for key in form_data.keys() if key.startswith('db_id_'))
            for idx in range(total_rows):
                if idx not in enrich_indexes: 
                    continue
                db_id = form_data.get(f"db_id_{idx}")
                fio = form_data.get(f"fio_{idx}")
                
                new_phone = clean_phone_number(form_data.get(f"new_phone_{idx}"))
                new_birthday = clean_birthday(form_data.get(f"new_birthday_{idx}"))
                
                db_phone = sanitize_value(form_data.get(f"db_phone_{idx}"))
                db_birthday = sanitize_value(form_data.get(f"db_birthday_{idx}"))
                
                cursor.execute('UPDATE Employee SET phone = %s, birthday = %s WHERE id = %s', (new_phone, new_birthday, db_id))
                
                changes = []
                if db_phone != new_phone: changes.append({"field": "phone", "old": db_phone, "new": new_phone})
                if db_birthday != (new_birthday or ""): changes.append({"field": "birthday", "old": db_birthday, "new": new_birthday})
                
                if changes:
                    log_change(conn, table_name='Employee', record_id=db_id, action='UPDATE', id_emp=user['id'], changes=changes, comment="Массовое обогащение данных")
                    log_msg = []
                    if db_phone != new_phone: log_msg.append(f"Телефон: {db_phone or '—'} ➔ {new_phone}")
                    if db_birthday != (new_birthday or ""): log_msg.append(f"ДР: {db_birthday or '—'} ➔ {new_birthday or '—'}")
                    report_list.append({"fio": fio, "details": ", ".join(log_msg)})
        conn.commit()
    except Exception as e:
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка записи обновлений: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    return templates.TemplateResponse(request=request, name="enrich_report.html", context={
        "report_list": report_list, "success_count": len(report_list), 
        "hide_details": len(report_list) > 10, "current_user": user
    })

if __name__ == "__main__":
    import uvicorn

    # Адрес и порт берём из config.ini, а не из текста программы.
    uvicorn.run(
        "main:app",
        host=WEB_CONFIG["host"],
        port=WEB_CONFIG["port"],
        reload=WEB_CONFIG["reload"],
    )