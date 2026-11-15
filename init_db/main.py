import os
import io
import math
import json
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor
from fastapi import FastAPI, Request, UploadFile, File, Form, Query, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from utils import (
    sanitize_value, clean_birthday, clean_phone_number, 
    generate_password, parse_fio_and_make_login, make_touchpoint_login, 
    normalize_name, transliterate
)
from init_db import log_change

# ==========================================
# 1. НАСТРОЙКИ И ПОДКЛЮЧЕНИЕ
# ==========================================
app = FastAPI(title="Worker Database System")
app.add_middleware(SessionMiddleware, secret_key="super-secret-key-12345")
app.mount("/static", StaticFiles(directory="static"), name="static")

PROJECTS_LIST = [
 "ATL_VTB", "CASTORAMA", "KOMUS_INVITRO", "MOESK", "MRG", "MTS", 
 "NEOVOX_MTS", "OZON", "OZON_MP", "TELETRIBE_DTSZN", "TELETRIBE_ESSM", 
 "TELETRIBE_MT", "TELETRIBE_POCHTA", "UNICREDIT_CHAT", "UNICREDIT_HL", 
 "UNICREDIT_KYC", "VOXYS_AVITO", "VOXYS_MOG", "VOXYS_RTK_IN", 
 "VOXYS_RZD", "VOXYS_TELE2", "VOXYS_VTB"
]

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(CURRENT_DIR, "templates"))

def get_db_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '192.168.10.220'),
        port=int(os.getenv('DB_PORT', '3308')),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', '2nsfa@423FdsC0fHvL'),
        database=os.getenv('DB_NAME', 'MariaDB_to_test_Anton'),
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False
    )

# ==========================================
# 2. АВТОРИЗАЦИЯ И СЕССИИ
# ==========================================

def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id: return None
    conn = get_db_connection()
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
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user

def get_all_job_titles():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, code, name FROM JobTitle ORDER BY id ASC")
            return cursor.fetchall()
    finally:
        conn.close()

def get_job_id_by_code(cursor, code="OP"):
    cursor.execute("SELECT id FROM JobTitle WHERE code = %s LIMIT 1", (code,))
    res = cursor.fetchone()
    return res['id'] if res else 7 

# ==========================================
# 3. ЛОГИКА ПРАВ ДОСТУПА (ИЕРАРХИЯ)
# ==========================================

def can_view_employee(cursor, current_user, target_emp_id):
    if current_user['id_role'] == 2: return True
    my_job = current_user['effective_job_code']
    if my_job in ['IT']: return True

    # Убрали AND e.is_deleted = 0, чтобы IT мог смотреть карточки удаленных
    cursor.execute("""
        SELECT e.id, jt.code as global_job, GROUP_CONCAT(DISTINCT pe.id_project) as projects,
               GROUP_CONCAT(DISTINCT jt_p.code) as project_jobs
        FROM Employee e
        LEFT JOIN JobTitle jt ON e.id_global_job = jt.id
        LEFT JOIN Project_Employee pe ON e.id = pe.id_emp AND pe.is_deleted = 0
        LEFT JOIN JobTitle jt_p ON pe.id_job = jt_p.id
        WHERE e.id = %s
        GROUP BY e.id
    """, (target_emp_id,))
    target = cursor.fetchone()
    if not target: return False

    t_global_job = target['global_job']
    t_project_jobs = set(target['project_jobs'].split(',')) if target['project_jobs'] else set()
    t_projects = set(target['projects'].split(',')) if target['projects'] else set()
    
    if t_global_job: t_project_jobs.add(t_global_job)
    if not t_project_jobs: t_project_jobs = {'OP'}

    if my_job == 'ADM':
        if 'ADM' in t_project_jobs or 'IT' in t_project_jobs: return False
        return True

    if my_job in ['RKT', 'SMM']:
        return t_project_jobs == {'OP'}

    cursor.execute("SELECT id_project FROM Project_Employee WHERE id_emp = %s AND is_deleted = 0", (current_user['id'],))
    my_projects = set(str(r['id_project']) for r in cursor.fetchall())
    
    if not my_projects and current_user['global_job_code']: return False
    has_shared_project = bool(my_projects.intersection(t_projects))
    
    # ЖЕСТКАЯ ИЕРАРХИЯ (свой уровень не видит свой уровень)
    if my_job == 'ST':
        allowed_targets = {'OP'}
    elif my_job == 'TR':
        allowed_targets = {'OP', 'ST'}
    elif my_job == 'SV':
        allowed_targets = {'OP', 'ST', 'TR'}
    elif my_job == 'RP':
        allowed_targets = {'OP', 'ST', 'TR', 'SV'}
    else:
        return False
        
    return has_shared_project and t_project_jobs.issubset(allowed_targets)

def can_edit_employee(cursor, current_user, target_emp_id):
    if current_user['id_role'] == 2: return True
    my_job = current_user['effective_job_code']
    if my_job == 'IT': return True

    cursor.execute("""
        SELECT GROUP_CONCAT(DISTINCT pe.id_project) as projects,
               GROUP_CONCAT(DISTINCT jt_p.code) as project_jobs
        FROM Employee e
        LEFT JOIN Project_Employee pe ON e.id = pe.id_emp AND pe.is_deleted = 0
        LEFT JOIN JobTitle jt_p ON pe.id_job = jt_p.id
        WHERE e.id = %s
        GROUP BY e.id
    """, (target_emp_id,))
    target = cursor.fetchone()
    if not target: return False
    
    t_project_jobs = set(target['project_jobs'].split(',')) if target['project_jobs'] else {'OP'}
    t_projects = set(target['projects'].split(',')) if target['projects'] else set()

    if my_job == 'ADM':
        return 'ADM' not in t_project_jobs and 'IT' not in t_project_jobs

    # ЖЕСТКАЯ ИЕРАРХИЯ РЕДАКТИРОВАНИЯ
    if my_job == 'SV':
        allowed_targets = {'OP', 'ST', 'TR'}
    elif my_job == 'RP':
        allowed_targets = {'OP', 'ST', 'TR', 'SV'}
    else:
        return False

    cursor.execute("SELECT id_project FROM Project_Employee WHERE id_emp = %s AND is_deleted = 0", (current_user['id'],))
    my_projects = set(str(r['id_project']) for r in cursor.fetchall())
    return bool(my_projects.intersection(t_projects)) and t_project_jobs.issubset(allowed_targets)

# ==========================================
# 4. ХЕЛПЕРЫ ДЛЯ РАБОТЫ С ДАННЫМИ
# ==========================================

def get_or_create_location(cursor, place_name: str) -> int:
    if not place_name: return None
    cursor.execute("SELECT id FROM Location WHERE name = %s", (place_name,))
    res = cursor.fetchone()
    if res: return res['id']
    cursor.execute("INSERT INTO Location (name) VALUES (%s)", (place_name,))
    return cursor.lastrowid

def sync_employee_projects(cursor, person_id: int, projects: list, job_title_id: int, login_ad: str):
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
    if request.session.get("user_id"): return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, login_ad: str = Form(...), password_ad: str = Form(...)):
    conn = get_db_connection()
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

    if user['effective_job_code'] not in ['IT', 'ADM'] and user['id_role'] != 2:
        return HTMLResponse("<h3>У вас нет прав на просмотр логов. Обратитесь к IT.</h3>", status_code=403)

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT a.id, a.changed_at, a.action, a.comment, a.changes,
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

            for log in logs:
                if log['changes']:
                    try:
                        changes_list = json.loads(log['changes'])
                        log['changes_formatted'] = ", ".join([f"{c['field']}: {c['old']} ➔ {c['new']}" for c in changes_list])
                    except:
                        log['changes_formatted'] = log['changes']
                else:
                    log['changes_formatted'] = ""

    finally:
        conn.close()

    return templates.TemplateResponse(request=request, name="logs.html", context={
        "current_user": user, "logs": logs,
        "date_from": date_from, "date_to": date_to,
        "target_fio": target_fio, "changer_fio": changer_fio,
        "project_filter": project_filter, "action_filter": action_filter,
        "project_list": PROJECTS_LIST, "page": page
    })

# ==========================================
# 7. ПОИСК И ГЛАВНАЯ
# ==========================================

def search_employees_advanced(current_user, query: str, status_filter: str, place_filter: list[str], project_filter: str, date_from: str, date_to: str, page: int, limit_val: str, sort_dir: str = "asc", show_archived: bool = False):
    conn = get_db_connection()
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
            params = []
            my_job = current_user['effective_job_code']
            my_id = current_user['id']

            # ФИЛЬТР АРХИВА
            if show_archived and (my_job in ['IT', 'ADM'] or current_user['id_role'] == 2):
                sql_select += " WHERE e.is_deleted = 1"
            else:
                sql_select += " WHERE e.is_deleted = 0"

            # ФИЛЬТР ПРАВ
            if current_user['id_role'] != 2 and my_job not in ['IT']:
                if my_job == 'ADM':
                    sql_select += " AND e.id_global_job NOT IN (SELECT id FROM JobTitle WHERE code IN ('ADM', 'IT'))"
                elif my_job in ['RKT', 'SMM']:
                    sql_select += """ AND EXISTS (
                        SELECT 1 FROM Project_Employee pe2 JOIN JobTitle jt2 ON pe2.id_job = jt2.id 
                        WHERE pe2.id_emp = e.id AND jt2.code = 'OP'
                    ) AND e.id_global_job IS NULL"""
                elif my_job in ['ST', 'TR', 'SV', 'RP']:
                    if my_job == 'RP':
                        allowed = "('OP', 'ST', 'TR', 'SV')"
                    elif my_job == 'SV':
                        allowed = "('OP', 'ST', 'TR')"
                    elif my_job == 'TR':
                        allowed = "('OP', 'ST')"
                    else: # ST
                        allowed = "('OP')"
                        
                    sql_select += f""" AND EXISTS (
                        SELECT 1 FROM Project_Employee pe_my 
                        JOIN Project_Employee pe_t ON pe_my.id_project = pe_t.id_project
                        JOIN JobTitle jt_t ON pe_t.id_job = jt_t.id
                        WHERE pe_my.id_emp = %s AND pe_t.id_emp = e.id AND jt_t.code IN {allowed}
                    ) AND e.id_global_job IS NULL"""
                    params.append(my_id)
                else:
                    return [], 0

            conditions = []
            if place_filter:
                extended_places = list(place_filter)
                if 'Дом' in place_filter and 'УРМ' not in extended_places: extended_places.append('УРМ')
                if 'УРМ' in place_filter and 'Дом' not in extended_places: extended_places.append('Дом')
                place_conds = " OR ".join(["loc.name = %s" for _ in extended_places])
                conditions.append(f"({place_conds})")
                params.extend(extended_places)
                
            if project_filter and project_filter != 'all':
                conditions.append("p.name = %s")
                params.append(project_filter)
                
            if query:
                query_clean = query.strip().lower()
                conditions.append("(LOWER(e.second_name) LIKE %s OR LOWER(e.login_ad) LIKE %s)")
                params.extend([f"%{query_clean}%", f"%{query_clean}%"])
                
            if status_filter and status_filter != 'all':
                conditions.append("e.id_status = %s")
                params.append(int(status_filter))
                
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
            total_found = cursor.fetchone()['cnt']
            
            direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
            sql_select += f" GROUP BY e.id ORDER BY e.second_name {direction}, e.first_name {direction}"
            
            if limit_val and limit_val != 'all':
                limit = int(limit_val)
                sql_select += " LIMIT %s OFFSET %s"
                params.extend([limit, (page - 1) * limit])
                
            cursor.execute(sql_select, tuple(params))
            raw_persons = cursor.fetchall()
            
            persons = []
            for p in raw_persons:
                p['places'] = p['group_places'].split(', ') if p['group_places'] else []
                p['projects'] = p['group_projects'].split(', ') if p['group_projects'] else []
                p['can_edit'] = can_edit_employee(cursor, current_user, p['id'])
                persons.append(p)
                
            return persons, total_found
    finally:
        conn.close()

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(require_login)):
    conn = get_db_connection()
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
        "current_page": 1, "limit": 50, "total_pages": 1, "project_list": PROJECTS_LIST,
        "job_titles": get_all_job_titles(), "current_user": user, "show_archived": False
    })

@app.get("/search", response_class=HTMLResponse)
def search(request: Request, user=Depends(require_login), 
           query: str = Query(None), status_filter: str = Query("all"), 
           place_filter: list[str] = Query([]), project_filter: str = Query("all"), 
           date_from: str = Query(None), date_to: str = Query(None), 
           page: int = Query(1), limit: str = Query("50"), sort_dir: str = Query("asc"),
           show_archived: str = Query("false")):
    
    search_query_clean = query.strip() if query else ""
    is_archived_view = (show_archived == "true")
    
    results, total_found = search_employees_advanced(
        user, search_query_clean, status_filter, place_filter, project_filter, 
        date_from, date_to, page, limit, sort_dir, is_archived_view
    )
    
    limit_numeric = int(limit) if limit != 'all' else 0
    total_pages = math.ceil(total_found / limit_numeric) if limit_numeric > 0 and total_found > 0 else 1
    
    conn = get_db_connection()
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
        "project_list": PROJECTS_LIST, "job_titles": get_all_job_titles(), "current_user": user,
        "show_archived": is_archived_view
    })

# ==========================================
# 8. КАРТОЧКА, ДОБАВЛЕНИЕ И РЕДАКТИРОВАНИЕ
# ==========================================

@app.get("/employee/{emp_id}")
def get_employee_details(emp_id: int, user=Depends(require_login)):
    conn = get_db_connection()
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
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cleaned_phone = clean_phone_number(phone)
            formatted_birthday = clean_birthday(birthday)
            
            second_name, first_name, curname, login_ad = generate_unique_login(cursor, fio)
            shared_password = generate_password()
            
            if job_title_id is None:
                job_title_id = get_job_id_by_code(cursor, "OP")
            
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
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if not can_edit_employee(cursor, user, emp_id):
                return {"error": "У вас нет прав на редактирование данных."}
                
            cursor.execute("SELECT id, second_name, first_name, curname, phone, id_status FROM Employee WHERE id = %s AND is_deleted = 0", (emp_id,))
            person = cursor.fetchone()
            if not person: return {"error": "Employee not found"}
            
            cursor.execute("SELECT id_project, id_job FROM Project_Employee WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['project_jobs'] = {r['id_project']: r['id_job'] for r in cursor.fetchall()}
            
            cursor.execute("SELECT id_loc FROM Location_Employee WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['selected_places'] = [r['id_loc'] for r in cursor.fetchall()]
            
            my_job = user['effective_job_code']
            
            if my_job == 'IT' or user['id_role'] == 2:
                cursor.execute("SELECT id, name FROM Project WHERE is_deleted = 0 ORDER BY name")
                allowed_projects = cursor.fetchall()
                cursor.execute("SELECT id, code, name FROM JobTitle ORDER BY id ASC")
                allowed_jobs = cursor.fetchall()
            elif my_job == 'ADM':
                cursor.execute("SELECT id, name FROM Project WHERE is_deleted = 0 ORDER BY name")
                allowed_projects = cursor.fetchall()
                cursor.execute("SELECT id, code, name FROM JobTitle WHERE code NOT IN ('IT', 'ADM') ORDER BY id ASC")
                allowed_jobs = cursor.fetchall()
            elif my_job in ['SV', 'RP']:
                cursor.execute("""
                    SELECT DISTINCT p.id, p.name FROM Project p
                    JOIN Project_Employee pe ON p.id = pe.id_project
                    JOIN JobTitle jt ON pe.id_job = jt.id
                    WHERE pe.id_emp = %s AND jt.code IN ('SV', 'RP') AND pe.is_deleted = 0
                    ORDER BY p.name
                """, (user['id'],))
                allowed_projects = cursor.fetchall()
                
                # SV может назначать до себя (ОП, СТ, ТР), а RP еще и СВ
                allowed_codes = "('OP', 'ST', 'TR', 'SV')" if my_job == 'RP' else "('OP', 'ST', 'TR')"
                cursor.execute(f"SELECT id, code, name FROM JobTitle WHERE code IN {allowed_codes} ORDER BY id ASC")
                allowed_jobs = cursor.fetchall()
            else:
                return {"error": "У вас нет прав на редактирование проектов."}

            return {"person": person, "all_projects": allowed_projects, "all_jobs": allowed_jobs}
    finally:
        conn.close()

@app.post("/employee/{emp_id}/update")
def update_employee(
    request: Request, emp_id: int, user=Depends(require_login),
    status_id: int = Form(...), phone: str = Form(None), 
    places: list[str] = Form([]), current_search: str = Form("")
):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            if not can_edit_employee(cursor, user, emp_id):
                return HTMLResponse("<script>alert('У вас нет прав на редактирование.'); window.history.back();</script>")

            cleaned_phone = clean_phone_number(phone)
            
            cursor.execute("SELECT phone, id_status, login_ad FROM Employee WHERE id = %s", (emp_id,))
            old_data = cursor.fetchone()
            
            cursor.execute("UPDATE Employee SET id_status = %s, phone = %s, updated_at = NOW(), updated_by = %s WHERE id = %s", (status_id, cleaned_phone, user['id'], emp_id))
            
            cursor.execute("""
                SELECT p.name, jt.code FROM Project_Employee pe
                JOIN Project p ON pe.id_project = p.id
                JOIN JobTitle jt ON pe.id_job = jt.id
                WHERE pe.id_emp = %s AND pe.is_deleted = 0
            """, (emp_id,))
            old_projects_list = [f"{r['name']} ({r['code']})" for r in cursor.fetchall()]
            
            cursor.execute("DELETE FROM Location_Employee WHERE id_emp = %s", (emp_id,))
            for place_name in places:
                loc_id = get_or_create_location(cursor, place_name)
                if loc_id:
                    cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, emp_id))
                
            cursor.execute("DELETE FROM Project_Employee WHERE id_emp = %s", (emp_id,))
            cursor.execute("DELETE FROM TouchPoint WHERE id_emp = %s", (emp_id,))
            
            form_data = request._form
            selected_projects = form_data.getlist("selected_projects")
            new_projects_list = []
            
            if old_data and old_data['login_ad']:
                for proj_id in selected_projects:
                    job_id = form_data.get(f"job_for_project_{proj_id}")
                    if not job_id: continue
                    
                    my_job = user['effective_job_code']
                    if my_job in ['SV', 'RP']:
                        cursor.execute("""
                            SELECT 1 FROM Project_Employee pe 
                            JOIN JobTitle jt ON pe.id_job = jt.id 
                            WHERE pe.id_emp = %s AND pe.id_project = %s AND jt.code IN ('SV', 'RP') AND pe.is_deleted = 0
                        """, (user['id'], proj_id))
                        if not cursor.fetchone(): continue
                            
                        cursor.execute("SELECT code FROM JobTitle WHERE id = %s", (job_id,))
                        target_job = cursor.fetchone()
                        
                        allowed_codes = ('OP', 'ST', 'TR', 'SV') if my_job == 'RP' else ('OP', 'ST', 'TR')
                        if not target_job or target_job['code'] not in allowed_codes: continue

                    cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, %s)", (proj_id, emp_id, job_id))
                    
                    cursor.execute("SELECT name FROM Project WHERE id = %s", (proj_id,))
                    proj_row = cursor.fetchone()
                    if proj_row:
                        proj_name = proj_row['name']
                        tp_login = make_touchpoint_login(old_data['login_ad'], proj_name)
                        if tp_login:
                            cursor.execute("INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) VALUES (%s, %s, %s)", (proj_id, emp_id, tp_login))
                            
                    cursor.execute("SELECT code FROM JobTitle WHERE id = %s", (job_id,))
                    job_code_row = cursor.fetchone()
                    if job_code_row:
                        new_projects_list.append(f"{proj_name} ({job_code_row['code']})")
                        
            changes = []
            if old_data and old_data['phone'] != cleaned_phone: changes.append({"field": "Телефон", "old": old_data['phone'], "new": cleaned_phone})
            if old_data and old_data['id_status'] != status_id: changes.append({"field": "Статус", "old": old_data['id_status'], "new": status_id})
            
            old_proj_str = ", ".join(sorted(old_projects_list)) if old_projects_list else "Нет"
            new_proj_str = ", ".join(sorted(new_projects_list)) if new_projects_list else "Нет"
            
            if old_proj_str != new_proj_str:
                changes.append({"field": "Проекты и Должности", "old": old_proj_str, "new": new_proj_str})
            
            if changes:
                log_change(conn, table_name='Employee', record_id=emp_id, action='UPDATE', id_emp=user['id'], changes=changes, comment="Ручное редактирование карточки")
                        
        conn.commit()
    except Exception as e: 
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка обновления: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    if current_search: 
        return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.delete("/employee/{emp_id}")
def delete_employee(emp_id: int, user=Depends(require_login)):
    conn = get_db_connection()
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
    if user['effective_job_code'] not in ['IT', 'ADM'] and user['id_role'] != 2:
        return {"status": "error", "error": "У вас нет прав на восстановление"}
        
    conn = get_db_connection()
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
    if not user['can_hard_delete']:
        return {"status": "error", "error": "У вас нет прав на физическое удаление"}
        
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
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
    try:
        parts = fio.split()
        if len(parts) < 2: return {"exists": False}
            
        second_name = normalize_name(parts[0])
        first_name = normalize_name(parts[1])
        curname = normalize_name(parts[2]) if len(parts) > 2 else ""
        
        conn = get_db_connection()
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
        df = pd.read_excel(io.BytesIO(contents), dtype=str)
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

    conn = get_db_connection()
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
def confirm_import(request: Request, user=Depends(require_login), import_indexes: list[int] = Form([])):
    form_data = request._form
    success_reports = []
    ignored_count = 0
    
    conn = get_db_connection()
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
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents), dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    has_fio = "фио" in df.columns
    has_split_name = "фамилия" in df.columns
    if not (has_fio or has_split_name):
        return HTMLResponse(content="<h3>Ошибка: В файле должны быть колонки 'ФИО' или 'Фамилия'</h3>", status_code=400)
        
    birthday_col = next((c for c in ['день рождения', 'дата рождения', 'др', 'рождение', 'birthday'] if c in df.columns), None)
    enrich_rows = []
    conn = get_db_connection()
    
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
def confirm_enrich(request: Request, user=Depends(require_login), enrich_indexes: list[int] = Form([])):
    form_data = request._form
    report_list = []
    conn = get_db_connection()
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

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)