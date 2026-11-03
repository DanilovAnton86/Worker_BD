import os
import re
import pandas as pd
import io
import math
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import pymysql
from pymysql.cursors import DictCursor

from utils import generate_password, parse_fio_and_make_login, make_touchpoint_login, normalize_name
from init_db import log_change # Импортируем функцию аудита из вашего файла БД

app = FastAPI(title="Worker Database System")
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

# 🔥 ЦЕНТРАЛИЗОВАННОЕ ПОДКЛЮЧЕНИЕ К MARIADB
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

# Временный ID "текущего пользователя" для аудита (позже заменим на сессию)
CURRENT_USER_ID = 1 

def get_all_job_titles():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, code, name FROM JobTitle ORDER BY id ASC")
            return cursor.fetchall()
    finally:
        conn.close()

def search_employees_advanced(query: str, status_filter: str, place_filter: list[str], project_filter: str, page: int, limit_val: str, sort_dir: str = "asc"):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql_select = """
                SELECT e.id, e.first_name, e.second_name, e.curname, e.login_ad, e.password_ad, 
                       e.phone, e.birthday, e.email_telescope, e.id_status, s.name AS status_name,
                       GROUP_CONCAT(DISTINCT loc.name SEPARATOR ', ') as group_places,
                       GROUP_CONCAT(DISTINCT CONCAT(p.name, ' (', jt.code, ')') SEPARATOR ', ') as group_projects
                FROM Employee e
                LEFT JOIN Status s ON e.id_status = s.id
                LEFT JOIN Location_Employee le ON e.id = le.id_emp AND le.is_deleted = 0
                LEFT JOIN Location loc ON le.id_loc = loc.id AND loc.is_deleted = 0
                LEFT JOIN Project_Employee pe ON e.id = pe.id_emp AND pe.is_deleted = 0
                LEFT JOIN Project p ON pe.id_project = p.id AND p.is_deleted = 0
                LEFT JOIN JobTitle jt ON pe.id_job = jt.id
                WHERE e.is_deleted = 0
            """
            
            conditions = []
            params = []
            
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
                persons.append(p)
                
            return persons, total_found
    finally:
        conn.close()

def get_employee_count():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as cnt FROM Employee WHERE is_deleted = 0")
            return cursor.fetchone()['cnt']
    finally:
        conn.close()

@app.get("/employee/{emp_id}")
def get_employee_details(emp_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT e.*, s.name as status_name FROM Employee e LEFT JOIN Status s ON e.id_status = s.id WHERE e.id = %s AND e.is_deleted = 0", (emp_id,))
            person = cursor.fetchone()
            if not person: return {"error": "Employee not found"}
            
            cursor.execute("SELECT l.name FROM Location l JOIN Location_Employee le ON l.id = le.id_loc WHERE le.id_emp = %s AND le.is_deleted = 0", (emp_id,))
            person['places'] = [r['name'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT p.name FROM Project p JOIN Project_Employee pe ON p.id = pe.id_project WHERE pe.id_emp = %s AND pe.is_deleted = 0", (emp_id,))
            person['projects'] = [r['name'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT name FROM TouchPoint WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['touchpoints'] = [r['name'] for r in cursor.fetchall()]
            
            return person
    finally:
        conn.close()

@app.delete("/employee/{emp_id}")
def delete_employee(emp_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Мягкое удаление (Soft Delete)
            cursor.execute("UPDATE Employee SET is_deleted = 1, deleted_at = NOW(), deleted_by = %s WHERE id = %s", (CURRENT_USER_ID, emp_id))
            
            # Запись в аудит
            log_change(conn, table_name='Employee', record_id=emp_id, action='SOFT_DELETE', id_emp=CURRENT_USER_ID, 
                       changes=[{"field": "is_deleted", "old": 0, "new": 1}], comment="Пометка об удалении")
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "total_count": get_employee_count(), "results": [], "search_query": "", 
        "status_filter": "all", "place_filter": [], "project_filter": "all", 
        "current_page": 1, "limit": 50, "total_pages": 1, "project_list": PROJECTS_LIST,
        "job_titles": get_all_job_titles() # 🔥 Передаем должности
    })

@app.get("/search", response_class=HTMLResponse)
def search(request: Request, query: str = Query(None), status_filter: str = Query("all"), place_filter: list[str] = Query([]), project_filter: str = Query("all"), page: int = Query(1), limit: str = Query("50"), sort_dir: str = Query("asc")):
    search_query_clean = query.strip() if query else ""
    results, total_found = search_employees_advanced(search_query_clean, status_filter, place_filter, project_filter, page, limit, sort_dir)
    limit_numeric = int(limit) if limit != 'all' else 0
    total_pages = math.ceil(total_found / limit_numeric) if limit_numeric > 0 and total_found > 0 else 1
    return templates.TemplateResponse(request=request, name="index.html", context={
        "total_count": get_employee_count(), "results": results, "search_query": search_query_clean, 
        "status_filter": status_filter, "place_filter": place_filter, "project_filter": project_filter, 
        "current_page": page, "limit": limit, "total_pages": total_pages, "sort_dir": sort_dir, "project_list": PROJECTS_LIST,
        "job_titles": get_all_job_titles() # 🔥 Передаем должности
    })

@app.post("/add_user")
def add_user(
    request: Request,
    fio: str = Form(...),
    phone: str = Form(None),
    birthday: str = Form(None),
    places: list[str] = Form([]),
    projects: list[str] = Form([]),
    job_title_id: int = Form(7),
    current_search: str = Form(""),
    is_lookalike: str = Form("false")
):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cleaned_phone = ""
            if phone:
                has_plus = phone.strip().startswith('+')
                cleaned_phone = ("+" if has_plus else "") + re.sub(r'\D', '', phone)
                cleaned_phone = cleaned_phone[:16] if has_plus else cleaned_phone[:15]
                
            formatted_birthday = birthday
            if birthday and '-' in birthday:
                dp = birthday.split('-')
                if len(dp) == 3: formatted_birthday = f"{dp[2]}.{dp[1]}.{dp[0]}"
                
            f_len, c_len = 1, 1
            digit_tail = ""
            
            while True:
                second_name, first_name, curname, login_ad = parse_fio_and_make_login(fio, f_len=f_len, c_len=c_len)
                if digit_tail: login_ad = f"{login_ad}{digit_tail}"
                
                email_telescope = f"{login_ad}@telescope.cc"
                email_cross = f"{login_ad}@cross.cc"
                
                cursor.execute('''
                    SELECT id FROM Employee 
                    WHERE login_ad = %s OR email_telescope = %s OR email_cross = %s
                ''', (login_ad, email_telescope, email_cross))
                
                if not cursor.fetchone(): break
                
                from utils import transliterate
                max_f_len = len(transliterate(first_name))
                max_c_len = len(transliterate(curname)) if curname else 0
                
                if f_len < max_f_len: f_len += 1
                elif max_c_len and c_len < max_c_len: c_len += 1
                else:
                    digit_tail = "1" if not digit_tail else str(int(digit_tail) + 1)
                        
            shared_password = generate_password()
            
            # Вставляем сотрудника (по умолчанию id_status=1, id_role=1)
            cursor.execute('''
                INSERT INTO Employee (first_name, second_name, curname, phone, birthday, login_ad, password_ad, email_telescope, email_cross, password_email, id_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
            ''', (first_name, second_name, curname, cleaned_phone, formatted_birthday, login_ad, shared_password, email_telescope, email_cross, shared_password))
            
            person_id = cursor.lastrowid
            
            # Логируем создание
            log_change(conn, table_name='Employee', record_id=person_id, action='INSERT', id_emp=CURRENT_USER_ID, comment="Создание сотрудника", changes=[])
            
            for place_name in places:
                if not place_name: continue
                cursor.execute("SELECT id FROM Location WHERE name = %s", (place_name,))
                res = cursor.fetchone()
                loc_id = res['id'] if res else None
                if not loc_id:
                    cursor.execute("INSERT INTO Location (name) VALUES (%s)", (place_name,))
                    loc_id = cursor.lastrowid
                cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, person_id))
                
            created_suffixes = set()
            for proj_name in projects:
                if not proj_name: continue
                cursor.execute("SELECT id FROM Project WHERE name = %s", (proj_name,))
                res = cursor.fetchone()
                proj_id = res['id'] if res else None
                if not proj_id:
                    cursor.execute("INSERT INTO Project (name) VALUES (%s)", (proj_name,))
                    proj_id = cursor.lastrowid
                    
                # Добавляем в проект с должностью OP (id_job = 7)
                cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, %s)", (proj_id, person_id, job_title_id))
                
                p_upper = proj_name.upper()
                suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
                if suffix and suffix not in created_suffixes:
                    tp_login = make_touchpoint_login(login_ad, proj_name)
                    if tp_login:
                        cursor.execute("INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) VALUES (%s, %s, %s)", (proj_id, person_id, tp_login))
                    created_suffixes.add(suffix)
                    
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
        "job_name": job_name  # 🔥 Передаем название должности
    })

@app.post("/enrich_file")
async def enrich_file(request: Request, file: UploadFile = File(...)):
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
                    s_name = normalize_name(str(row.get('фамилия', '')))
                    f_name = normalize_name(str(row.get('имя', '')))
                    c_name = normalize_name(str(row.get('отчество', '')))
                else:
                    fio_str = str(row.get('фио', '')).strip()
                    fio_parts = fio_str.split()
                    if len(fio_parts) < 1: continue
                    s_name = normalize_name(fio_parts[0])
                    f_name = normalize_name(fio_parts[1]) if len(fio_parts) > 1 else ""
                    c_name = normalize_name(fio_parts[2]) if len(fio_parts) > 2 else ""
        
                if not s_name or s_name == 'Nan': continue
                
                new_phone = str(row.get('телефон', row.get('phone', ''))).strip()
                if new_phone.lower() in ['nan', 'none']: new_phone = ""
                if new_phone.endswith('.0'): new_phone = new_phone[:-2]
                if new_phone:
                    has_plus = new_phone.startswith('+')
                    new_phone = ("+" if has_plus else "") + re.sub(r'\D', '', new_phone)
                    new_phone = new_phone[:16] if has_plus else new_phone[:15]
        
                new_birthday = ""
                if birthday_col:
                    raw_bday = str(row.get(birthday_col, '')).strip()
                    if raw_bday.lower() not in ['nan', 'none', '']:
                        if ' ' in raw_bday: raw_bday = raw_bday.split()[0]
                        raw_bday = raw_bday.replace('-', '.').replace(',', '.')
                        raw_bday = "".join(ch for ch in raw_bday if ch in "0123456789.")
                        dp = raw_bday.split('.')
                        if len(dp) == 3 and len(dp[0]) == 4: new_birthday = f"{dp[2]}-{dp[1]}-{dp[0]}" # Для MariaDB нужен формат YYYY-MM-DD
                        else: new_birthday = raw_bday[:10]
        
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
                    
                    # Приводим дату из базы к строке для сравнения
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
    return templates.TemplateResponse(request=request, name="enrich_preview.html", context={"rows": enrich_rows, "total_count": len(enrich_rows)})

@app.post("/confirm_enrich", response_class=HTMLResponse)
def confirm_enrich(request: Request, enrich_indexes: list[int] = Form([])):
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
                new_phone = form_data.get(f"new_phone_{idx}", "").strip()
                new_birthday = form_data.get(f"new_birthday_{idx}", "").strip()
                db_phone = form_data.get(f"db_phone_{idx}", "").strip()
                db_birthday = form_data.get(f"db_birthday_{idx}", "").strip()
                
                # Форматируем дату для MariaDB (YYYY-MM-DD), если она есть
                db_birthday_val = new_birthday if new_birthday else None
                
                cursor.execute('UPDATE Employee SET phone = %s, birthday = %s WHERE id = %s', (new_phone, db_birthday_val, db_id))
                
                changes = []
                if db_phone != new_phone: changes.append({"field": "phone", "old": db_phone, "new": new_phone})
                if db_birthday != new_birthday: changes.append({"field": "birthday", "old": db_birthday, "new": new_birthday})
                
                if changes:
                    log_change(conn, table_name='Employee', record_id=db_id, action='UPDATE', id_emp=CURRENT_USER_ID, changes=changes, comment="Массовое обогащение данных")
                    log_msg = []
                    if db_phone != new_phone: log_msg.append(f"Телефон: {db_phone or '—'} ➔ {new_phone}")
                    if db_birthday != new_birthday: log_msg.append(f"ДР: {db_birthday or '—'} ➔ {new_birthday}")
                    report_list.append({"fio": fio, "details": ", ".join(log_msg)})
        conn.commit()
    except Exception as e:
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка записи обновлений: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    return templates.TemplateResponse(request=request, name="enrich_report.html", context={"report_list": report_list, "success_count": len(report_list), "hide_details": len(report_list) > 10})

@app.post("/import_file")
async def import_file(
    request: Request,
    file: UploadFile = File(...),
    default_project: str = Form(None),
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
                "phone": "", "birthday": "",
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
                s_name = normalize_name(str(row.get('фамилия', '')))
                f_name = normalize_name(str(row.get('имя', '')))
                c_name = normalize_name(str(row.get('отчество', '')))
            else:
                fio_str = str(row.get('фио', '')).strip()
                fio_parts = fio_str.split()
                if len(fio_parts) < 2: continue
                s_name = normalize_name(fio_parts[0])
                f_name = normalize_name(fio_parts[1])
                c_name = normalize_name(fio_parts[2]) if len(fio_parts) > 2 else ""

            if not s_name or not f_name or s_name == 'Nan' or f_name == 'Nan':
                continue

            phone_val = str(row.get('телефон', row.get('phone', ''))).strip()
            phone_val = "" if phone_val.lower() in ['nan', 'none'] else (phone_val[:-2] if phone_val.endswith('.0') else phone_val)
            
            birthday_val = None
            if birthday_col:
                raw_bday = str(row.get(birthday_col, '')).strip()
                if raw_bday.lower() not in ['nan', 'none', '']:
                    if ' ' in raw_bday: raw_bday = raw_bday.split()[0]
                    raw_bday = raw_bday.replace('-', '.').replace(',', '.')
                    dp = "".join(ch for ch in raw_bday if ch in "0123456789.").split('.')
                    if len(dp) == 3:
                        birthday_val = f"{dp[2]}-{dp[1]}-{dp[0]}" if len(dp[0]) == 2 else f"{dp[0]}-{dp[1]}-{dp[2]}"

            proj_val = str(row.get('проект', row.get('project', ''))).strip()
            place_val = str(row.get('локация', row.get('location', ''))).strip()
            
            row_projects = [proj_val] if proj_val and proj_val.lower() != 'nan' else ([default_project] if default_project else [])
            row_places = ['Дом'] if place_val.upper() in ['УРМ', 'ДОМ'] else (['Офис'] if place_val.upper() == 'ОФИС' else ([place_val] if place_val and place_val.lower() != 'nan' else default_places))

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
    
    return templates.TemplateResponse(request=request, name="import_preview.html", context={"rows": parsed_rows, "total_count": len(parsed_rows)})

@app.post("/confirm_import", response_class=HTMLResponse)
def confirm_import(request: Request, import_indexes: list[int] = Form([])):
    form_data = request._form
    success_reports = []
    ignored_count = 0
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            total_rows = sum(1 for key in form_data.keys() if key.startswith('second_name_'))
            
            for idx in range(total_rows):
                if idx not in import_indexes:
                    ignored_count += 1
                    continue
                    
                second_name = form_data.get(f"second_name_{idx}", "").strip()
                first_name = form_data.get(f"first_name_{idx}", "").strip()
                curname = form_data.get(f"curname_{idx}", "").strip()
                phone = form_data.get(f"phone_{idx}", "").strip()
                birthday = form_data.get(f"birthday_{idx}", "").strip()
                projects = form_data.getlist(f"projects_{idx}")
                places = form_data.getlist(f"places{idx}")
                
                from utils import transliterate
                s_name_trans = transliterate(second_name)
                f_name_trans = transliterate(first_name)
                c_name_trans = transliterate(curname) if curname else ""
                f_len, c_len, digit_tail = 1, 1, ""
                
                while True:
                    f_part = f_name_trans[:f_len]
                    c_part = c_name_trans[:c_len] if c_name_trans else ""
                    
                    if c_part: login_ad = f"{f_part}.{c_part}.{s_name_trans}{digit_tail}"
                    else: login_ad = f"{f_part}.{s_name_trans}{digit_tail}"
                        
                    cursor.execute('SELECT id FROM Employee WHERE login_ad = %s OR email_telescope = %s OR email_cross = %s', (login_ad, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc"))
                    if not cursor.fetchone(): break
                        
                    if f_len < len(f_name_trans): f_len += 1
                    elif c_name_trans and c_len < len(c_name_trans): c_len += 1
                    else: digit_tail = "1" if not digit_tail else str(int(digit_tail) + 1)
                    
                shared_password = generate_password()
                
                db_birthday = birthday if birthday else None
                
                cursor.execute('''
                    INSERT INTO Employee (first_name, second_name, curname, phone, birthday, login_ad, password_ad, email_telescope, email_cross, password_email, id_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                ''', (first_name, second_name, curname, phone, db_birthday, login_ad, shared_password, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc", shared_password))
                
                person_id = cursor.lastrowid
                log_change(conn, table_name='Employee', record_id=person_id, action='INSERT', id_emp=CURRENT_USER_ID, comment="Импорт сотрудника из файла")
                
                for place_name in places:
                    if not place_name: continue
                    cursor.execute("SELECT id FROM Location WHERE name = %s", (place_name,))
                    res = cursor.fetchone()
                    loc_id = res['id'] if res else None
                    if not loc_id:
                        cursor.execute("INSERT INTO Location (name) VALUES (%s)", (place_name,))
                        loc_id = cursor.lastrowid
                    cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, person_id))
                    
                created_suffixes = set()
                for proj_name in projects:
                    if not proj_name: continue
                    cursor.execute("SELECT id FROM Project WHERE name = %s", (proj_name,))
                    res = cursor.fetchone()
                    proj_id = res['id'] if res else None
                    if not proj_id:
                        cursor.execute("INSERT INTO Project (name) VALUES (%s)", (proj_name,))
                        proj_id = cursor.lastrowid
                        
                    cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, 7)", (proj_id, person_id))
                    
                    p_upper = proj_name.upper()
                    suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
                    if suffix and suffix not in created_suffixes:
                        tp_login = make_touchpoint_login(login_ad, proj_name)
                        if tp_login: 
                            cursor.execute("INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) VALUES (%s, %s, %s)", (proj_id, person_id, tp_login))
                        created_suffixes.add(suffix)
                        
                success_reports.append({
                    "fio": f"{second_name} {first_name} {curname}", 
                    "login": login_ad, 
                    "password": shared_password,
                    "projects_list": projects,  # 🔥 Передаем список проектов
                    "job_name": "OP (Оператор)"  # 🔥 Указываем должность по умолчанию
                })
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка импорта: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    return templates.TemplateResponse(request=request, name="import_report.html", context={"success_list": success_reports, "success_count": len(success_reports), "ignored_count": ignored_count})

@app.get("/employee/{emp_id}/edit_data")
def get_edit_data(emp_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, second_name, first_name, curname, phone, id_status FROM Employee WHERE id = %s AND is_deleted = 0", (emp_id,))
            person = cursor.fetchone()
            if not person: return {"error": "Employee not found"}
            
            cursor.execute("SELECT id_project FROM Project_Employee WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['selected_projects'] = [r['id_project'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT id_loc FROM Location_Employee WHERE id_emp = %s AND is_deleted = 0", (emp_id,))
            person['selected_places'] = [r['id_loc'] for r in cursor.fetchall()]
            
            cursor.execute("SELECT id, name FROM Project WHERE is_deleted = 0")
            all_projects = cursor.fetchall()
            
            return {"person": person, "all_projects": all_projects}
    finally:
        conn.close()

@app.get("/check_duplicate")
def check_duplicate(fio: str):
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

@app.post("/employee/{emp_id}/update")
def update_employee(
    emp_id: int, 
    status: int = Form(...), 
    phone: str = Form(None), 
    places: list[str] = Form([]), 
    projects: list[int] = Form([]), 
    current_search: str = Form("")
):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cleaned_phone = re.sub(r'\D', '', phone) if phone else ""
            
            cursor.execute("SELECT phone, id_status FROM Employee WHERE id = %s", (emp_id,))
            old_data = cursor.fetchone()
            
            cursor.execute("UPDATE Employee SET id_status = %s, phone = %s, updated_at = NOW(), updated_by = %s WHERE id = %s", (status, cleaned_phone, CURRENT_USER_ID, emp_id))
            
            changes = []
            if old_data and old_data['phone'] != cleaned_phone: changes.append({"field": "phone", "old": old_data['phone'], "new": cleaned_phone})
            if old_data and old_data['id_status'] != status: changes.append({"field": "id_status", "old": old_data['id_status'], "new": status})
            
            if changes:
                log_change(conn, table_name='Employee', record_id=emp_id, action='UPDATE', id_emp=CURRENT_USER_ID, changes=changes, comment="Ручное редактирование карточки")
            
            # Локации
            cursor.execute("DELETE FROM Location_Employee WHERE id_emp = %s", (emp_id,))
            for place_name in places:
                if not place_name: continue
                cursor.execute("SELECT id FROM Location WHERE name = %s", (place_name,))
                res = cursor.fetchone()
                loc_id = res['id'] if res else None
                if not loc_id:
                    cursor.execute("INSERT INTO Location (name) VALUES (%s)", (place_name,))
                    loc_id = cursor.lastrowid
                cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s)", (loc_id, emp_id))
                
            # Проекты
            cursor.execute("DELETE FROM Project_Employee WHERE id_emp = %s", (emp_id,))
            for proj_id in projects: 
                # По умолчанию ставим OP (id=7)
                cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, 7)", (proj_id, emp_id))
                
            # Пересчитываем TouchPoint
            cursor.execute("DELETE FROM TouchPoint WHERE id_emp = %s", (emp_id,))
            cursor.execute("SELECT login_ad FROM Employee WHERE id = %s", (emp_id,))
            login_row = cursor.fetchone()
            login_ad = login_row['login_ad'] if login_row else None
            
            if login_ad:
                created_suffixes = set()
                for proj_id in projects:
                    cursor.execute("SELECT name FROM Project WHERE id = %s", (proj_id,))
                    proj_row = cursor.fetchone()
                    if not proj_row: continue
                    proj_name = proj_row['name']
                    
                    p_upper = proj_name.upper()
                    suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
                    if suffix and suffix not in created_suffixes:
                        tp_login = make_touchpoint_login(login_ad, proj_name)
                        if tp_login: 
                            cursor.execute("INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) VALUES (%s, %s, %s)", (proj_id, emp_id, tp_login))
                        created_suffixes.add(suffix)
                        
        conn.commit()
    except Exception as e: 
        conn.rollback()
        return HTMLResponse(content=f"<h3>Ошибка обновления: {e}</h3>", status_code=400)
    finally:
        conn.close()
        
    if current_search: 
        return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)