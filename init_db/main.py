import os
import sqlite3
import re
import pandas as pd
import io
import math
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse  # <-- Перенесли RedirectResponse сюда
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from utils import generate_password, parse_fio_and_make_login, make_touchpoint_login

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
DB_PATH = os.path.join(CURRENT_DIR, 'UZ_OP.db')

# 🔥 ЦЕНТРАЛИЗОВАННОЕ ПОДКЛЮЧЕНИЕ (Подготовка к MariaDB)
def get_db_connection():
    """Генератор подключения к БД. При переходе на MariaDB меняется ТОЛЬКО этот код"""
    conn = sqlite3.connect(DB_PATH)
    conn.create_function("LOWER_RU", 1, lambda x: str(x).lower() if x is not None else "")
    conn.row_factory = sqlite3.Row
    return conn

def search_employees_advanced(query: str, status_filter: str, place_filter: list[str], project_filter: str, page: int, limit_val: str, sort_dir: str = "asc"):
    if not os.path.exists(DB_PATH):
        return [], 0
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # ИСПОЛЬЗУЕМ СУПЕР-ЗАПРОС С GROUP_CONCAT (Уничтожаем 200 лишних циклов)
    sql_select = """
        SELECT DISTINCT p.id, p.first_name, p.second_name, p.curname, p.login_ad, p.password_ad, p.phone, p.birthday, p.email_telescope, p.status,
        GROUP_CONCAT(DISTINCT pl.name) as group_places,
        GROUP_CONCAT(DISTINCT pr.name) as group_projects
        FROM Person p
        LEFT JOIN Place_Person pp ON p.id = pp.id_per
        LEFT JOIN Place pl ON pp.id_pl = pl.id
        LEFT JOIN Project_Person prp ON p.id = prp.id_per
        LEFT JOIN Project pr ON prp.id_pr = pr.id
    """
    
    conditions = []
    params = []
    
    if place_filter:
        extended_places = list(place_filter)
        if 'Дом' in place_filter and 'УРМ' not in extended_places: extended_places.append('УРМ')
        if 'УРМ' in place_filter and 'Дом' not in extended_places: extended_places.append('Дом')
        place_conds = " OR ".join("pl.name = ?" for _ in extended_places)
        conditions.append(f"({place_conds})")
        params.extend(extended_places)
        
    if project_filter and project_filter != 'all':
        conditions.append("pr.name = ?")
        params.append(project_filter)
        
    if query:
        conditions.append("(LOWER_RU(p.second_name) LIKE ? OR LOWER_RU(p.login_ad) LIKE ?)")
        query_lower = query.strip().lower()
        params.extend([f"%{query_lower}%", f"%{query_lower}%"])
        
    if status_filter and status_filter != 'all':
        conditions.append("p.status = ?")
        params.append(int(status_filter))
        
    if conditions:
        sql_select += " WHERE " + " AND ".join(conditions)
        
    sql_select += " GROUP BY p.id"
    
    # 1. Считаем общее число уникальных строк для пагинации
    count_query = f"SELECT COUNT(*) FROM ({sql_select});"
    cursor.execute(count_query, tuple(params))
    res_count = cursor.fetchone()
    total_found = res_count[0] if res_count else 0
    
    # 2. Применяем сортировку и лимиты страниц
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    sql_select += f" ORDER BY p.second_name {direction}, p.first_name {direction}"
    
    if limit_val and limit_val != 'all':
        limit = int(limit_val)
        sql_select += " LIMIT ? OFFSET ?"
        params.extend([limit, (page - 1) * limit])
        
    cursor.execute(sql_select + ";", tuple(params))
    raw_persons = cursor.fetchall()
    conn.close()
    
    # Быстро раскладываем склеенные базой строки обратно в массивы для Jinja2
    persons = []
    for row in raw_persons:
        p = dict(row)
        p['places'] = p['group_places'].split(',') if p['group_places'] else []
        p['projects'] = p['group_projects'].split(',') if p['group_projects'] else []
        persons.append(p)
        
    return persons, total_found

def get_employee_count():
    if not os.path.exists(DB_PATH): return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Person;")
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0

@app.get("/employee/{emp_id}")
def get_employee_details(emp_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Person WHERE id = ?;", (emp_id,))
    person_row = cursor.fetchone()
    if not person_row:
        conn.close()
        return {"error": "Employee not found"}
    person = dict(person_row)
    
    cursor.execute("SELECT p.name FROM Place p JOIN Place_Person pp ON p.id = pp.id_pl WHERE pp.id_per = ?;", (emp_id,))
    person['places'] = [r['name'] for r in cursor.fetchall()]
    
    cursor.execute("SELECT pr.name FROM Project pr JOIN Project_Person pp ON pr.id = pp.id_pr WHERE pp.id_per = ?;", (emp_id,))
    person['projects'] = [r['name'] for r in cursor.fetchall()]
    
    cursor.execute('SELECT name FROM TouchPoint WHERE id_per = ?;', (emp_id,))
    person['touchpoints'] = [r['name'] for r in cursor.fetchall()]
    conn.close()
    return person

@app.delete("/employee/{emp_id}")
def delete_employee(emp_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("DELETE FROM Person WHERE id = ?;", (emp_id,))
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        return {"status": "success"} if success else {"status": "error", "error": "Сотрудник не найден"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "total_count": get_employee_count(), "results": [], "search_query": "", 
        "status_filter": "all", "place_filter": [], "project_filter": "all", 
        "current_page": 1, "limit": 50, "total_pages": 1, "project_list": PROJECTS_LIST
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
        "current_page": page, "limit": limit, "total_pages": total_pages, "sort_dir": sort_dir, "project_list": PROJECTS_LIST
    })

@app.post("/add_user")
def add_user(request: Request, fio: str = Form(...), phone: str = Form(None), birthday: str = Form(None), places: list[str] = Form([]), projects: list[str] = Form([]), current_search: str = Form(""), is_lookalike: str = Form("false")):
    try:
        cleaned_phone = ""
        if phone:
            has_plus = phone.strip().startswith('+')
            cleaned_phone = ("+" if has_plus else "") + re.sub(r'\D', '', phone)
            cleaned_phone = cleaned_phone[:16] if has_plus else cleaned_phone[:15]
            
        formatted_birthday = birthday
        if birthday and '-' in birthday:
            dp = birthday.split('-')
            if len(dp) == 3: formatted_birthday = f"{dp[2]}.{dp[1]}.{dp[0]}"
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        f_len, c_len = 1, 1
        
        while True:
            second_name, first_name, curname, login_ad = parse_fio_and_make_login(fio, f_len=f_len, c_len=c_len)
            email_telescope, email_cross = f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc"
            
            cursor.execute('SELECT id FROM Person WHERE login_ad = ? OR email_telescope = ? OR email_cross = ?;', (login_ad, email_telescope, email_cross))
            if not cursor.fetchone(): break
            
            if is_lookalike.lower() != "true":
                conn.close()
                return HTMLResponse(content="<h3>Ошибка: Логин уже существует. Подтвердите дубликат.</h3>", status_code=400)
                
            from utils import transliterate
            if f_len < len(transliterate(first_name)): f_len += 1
            elif curname and c_len < len(transliterate(curname)): c_len += 1
            else:
                conn.close()
                return HTMLResponse(content="<h3>Ошибка: Не удалось расширить буквенный логин. Клон в БД.</h3>", status_code=400)
                
        # Генерируем один общий пароль
        shared_password = generate_password()
        
        # Безопасная запись в таблицу Person
        cursor.execute('''
            INSERT INTO Person (
                first_name, second_name, curname, phone, birthday, 
                login_ad, password_ad, email_telescope, email_cross, password_email, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
        ''', (first_name, second_name, curname, cleaned_phone, formatted_birthday, 
              login_ad, shared_password, email_telescope, email_cross, shared_password))
        
        person_id = cursor.lastrowid

        
        # Привязка локаций (Place)
        for place_name in places:
            cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
            res = cursor.fetchone()
            place_id = res[0] if res else None
            if not place_id:
                cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                place_id = cursor.lastrowid
            cursor.execute("INSERT INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, person_id))
            
        # Привязка проектов и создание TouchPoint без дубликатов
        created_suffixes = set()
        for proj_name in projects:
            cursor.execute("SELECT id FROM Project WHERE name = ?;", (proj_name,))
            res = cursor.fetchone()
            proj_id = res[0] if res else None
            if not proj_id:
                cursor.execute("INSERT INTO Project (name) VALUES (?);", (proj_name,))
                proj_id = cursor.lastrowid
                
            cursor.execute("INSERT INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, person_id))
            
            p_upper = proj_name.upper()
            suffix = ""
            if "UNICREDIT" in p_upper: suffix = "_unic"
            elif "MRG" in p_upper: suffix = "_mrg"
            elif "OZON" in p_upper: suffix = "_ozon"
            
            if suffix and suffix not in created_suffixes:
                tp_login = make_touchpoint_login(login_ad, proj_name)
                if tp_login:
                    cursor.execute("INSERT OR IGNORE INTO TouchPoint (id_pr, id_per, name) VALUES (?, ?, ?);", 
                                   (proj_id, person_id, tp_login))
                    created_suffixes.add(suffix)
                    
        conn.commit()
        conn.close()
        
    except Exception as e:
        if 'conn' in locals(): conn.close()
        print(f"Ошибка при добавлении: {e}")
        return HTMLResponse(content=f"<h3>Ошибка базы данных: {e}</h3>", status_code=400)
        
    # ВМЕСТО РЕДИРЕКТА: Отдаем красивую карточку доступов для менеджера
    return templates.TemplateResponse(
        request=request,
        name="add_report.html",
        context={
            "second_name": second_name,
            "first_name": first_name,
            "curname": curname,
            "places": places,
            "projects": projects,
            "login_ad": login_ad,
            "password": shared_password
        }
    )

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
    
    # 1. ОБРАБОТКА ТЕКСТОВОГО ФАЙЛА (.TXT)
    if filename.endswith('.txt'):
        try:
            text = contents.decode('utf-8')
        except UnicodeDecodeError:
            text = contents.decode('cp1251')
            
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if not line: continue
                
            parts = [p.strip() for p in line.split() if p.strip()]
            if len(parts) < 2: continue # Защита: шлём в пень без Фамилии и Имени
                
            parsed_rows.append({
                "second_name": parts[0].capitalize(),
                "first_name": parts[1].capitalize(),
                "curname": parts[2].capitalize() if len(parts) > 2 else "",
                "phone": "", 
                "birthday": "",
                "projects": [default_project] if default_project else [],
                "places": default_places
            })

    # 2. ОБРАБОТКА EXCEL ФАЙЛА (.XLSX)
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
                s_name = str(row.get('фамилия', '')).strip()
                f_name = str(row.get('имя', '')).strip()
                c_name = str(row.get('отчество', '')).strip()
                if c_name.lower() in ['nan', 'none', '']: c_name = ""
            else:
                fio_str = str(row.get('фио', '')).strip()
                fio_parts = [p.strip() for p in fio_str.split() if p.strip()]
                if len(fio_parts) < 2: continue
                s_name = fio_parts[0]
                f_name = fio_parts[1]
                c_name = fio_parts[2] if len(fio_parts) > 2 else ""

            if not s_name or not f_name or s_name.lower() == 'nan' or f_name.lower() == 'nan':
                continue

            phone_val = str(row.get('телефон', row.get('phone', ''))).strip()
            phone_val = "" if phone_val.lower() in ['nan', 'none'] else (phone_val[:-2] if phone_val.endswith('.0') else phone_val)
            
            birthday_val = ""
            if birthday_col:
                raw_bday = str(row.get(birthday_col, '')).strip()
                if raw_bday.lower() not in ['nan', 'none', '']:
                    if ' ' in raw_bday: raw_bday = raw_bday.split()[0]
                    raw_bday = raw_bday.replace('-', '.').replace(',', '.')
                    birthday_val = "".join(ch for ch in raw_bday if ch in "0123456789.")[:10]

            proj_val = str(row.get('проект', row.get('project', ''))).strip()
            place_val = str(row.get('локация', row.get('location', ''))).strip()
            
            row_projects = [proj_val] if proj_val and proj_val.lower() != 'nan' else ([default_project] if default_project else [])
            row_places = ['Дом'] if place_val.upper() in ['УРМ', 'ДОМ'] else (['Офис'] if place_val.upper() == 'ОФИС' else ([place_val] if place_val and place_val.lower() != 'nan' else default_places))

            parsed_rows.append({
                "second_name": s_name.capitalize(), 
                "first_name": f_name.capitalize(),
                "curname": c_name.capitalize() if c_name else "", 
                "phone": phone_val,
                "birthday": birthday_val, 
                "projects": row_projects, 
                "places": row_places
            })
    else:
        return HTMLResponse(content="<h3>Ошибка: Неподдерживаемый формат файла</h3>", status_code=400)

    # 3. АНАЛИЗ ТЁЗОК ПО НАШИМ ТРЕМ СИТУАЦИЯМ (Переведено на центральный коннект)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for person in parsed_rows:
        cursor.execute('''
            SELECT id, second_name, first_name, curname, phone, birthday 
            FROM Person 
            WHERE second_name = ? AND first_name = ?;
        ''', (person['second_name'], person['first_name']))
        
        matches = [dict(r) for r in cursor.fetchall()]
        person['is_duplicate'] = False
        person['db_match_details'] = ""
        
        for m in matches:
            db_curname = m['curname'] or ""
            file_curname = person['curname'] or ""
            
            if db_curname and file_curname and db_curname != file_curname:
                continue # Ситуация 1: разные отчества, пропускаем
            elif db_curname == file_curname:
                person['is_duplicate'] = True
                person['db_match_details'] = f"Полный тёзка в БД: {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                break # Ситуация 3: полный тёзка
            elif not db_curname or not file_curname:
                person['is_duplicate'] = True
                person['db_match_details'] = f"Подозрение на дубликат (нет отчества): {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                break # Ситуация 2: одно отчество пустое
                
    conn.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="import_preview.html", 
        context={"rows": parsed_rows, "total_count": len(parsed_rows)}
    )

@app.post("/confirm_import", response_class=HTMLResponse)
def confirm_import(request: Request, import_indexes: list[int] = Form([])):
    if not os.path.exists(DB_PATH): 
        return HTMLResponse(content="<h3>Ошибка: База данных не найдена</h3>", status_code=400)
        
    form_data = request._form
    success_reports = []
    ignored_count = 0
    
    try:
        # ОПТИМИЗАЦИЯ: перешли на центральный коннект, готовый к MariaDB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # Считаем общее число строк в пришедшей форме по скрытым полям Фамилии
        total_rows = sum(1 for key in form_data.keys() if key.startswith('second_name_'))
        
        for idx in range(total_rows):
            # Если менеджер снял галочку с этой строки — пропускаем её
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
                
                if c_part:
                    login_ad = f"{f_part}.{c_part}.{s_name_trans}{digit_tail}"
                else:
                    login_ad = f"{f_part}.{s_name_trans}{digit_tail}"
                    
                cursor.execute('SELECT id FROM Person WHERE login_ad = ? OR email_telescope = ? OR email_cross = ?;', 
                               (login_ad, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc"))
                if not cursor.fetchone(): 
                    break # Логин и почты свободны!
                    
                if f_len < len(f_name_trans): f_len += 1
                elif c_name_trans and c_len < len(c_name_trans): c_len += 1
                else: digit_tail = "1" if not digit_tail else str(int(digit_tail) + 1)
                
            shared_password = generate_password()
            cursor.execute('''
                INSERT INTO Person (
                    first_name, second_name, curname, phone, birthday, 
                    login_ad, password_ad, email_telescope, email_cross, password_email, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
            ''', (first_name, second_name, curname, phone, birthday, 
                  login_ad, shared_password, f"{login_ad}@telescope.cc", f"{login_ad}@cross.cc", shared_password))
            
            person_id = cursor.lastrowid
            
            # ИСПРАВЛЕНИЕ ТИПОВ ДАННЫХ: строго res[0] для извлечения числа
            for place_name in places:
                if not place_name: continue
                cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
                res = cursor.fetchone()
                place_id = res[0] if res else None
                if not place_id:
                    cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                    place_id = cursor.lastrowid
                cursor.execute("INSERT OR IGNORE INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, person_id))
                
            # ИСПРАВЛЕНИЕ: Оставили одно правильное объявление множества суффиксов
            created_suffixes = set()
            for proj_name in projects:
                if not proj_name: continue
                cursor.execute("SELECT id FROM Project WHERE name = ?;", (proj_name,))
                res = cursor.fetchone()
                proj_id = res[0] if res else None
                if not proj_id:
                    cursor.execute("INSERT INTO Project (name) VALUES (?);", (proj_name,))
                    proj_id = cursor.lastrowid
                cursor.execute("INSERT OR IGNORE INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, person_id))
                
                p_upper = proj_name.upper()
                suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
                if suffix and suffix not in created_suffixes:
                    tp_login = make_touchpoint_login(login_ad, proj_name)
                    if tp_login: 
                        cursor.execute("INSERT OR IGNORE INTO TouchPoint (id_pr, id_per, name) VALUES (?, ?, ?);", 
                                       (proj_id, person_id, tp_login))
                    created_suffixes.add(suffix)
                    
            success_reports.append({
                "fio": f"{second_name} {first_name} {curname}", 
                "login": login_ad, 
                "password": shared_password
            })
            
        conn.commit()
        conn.close()
        
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return HTMLResponse(content=f"<h3>Ошибка импорта: {e}</h3>", status_code=400)
        
    return templates.TemplateResponse(
        request=request, 
        name="import_report.html", 
        context={
            "success_list": success_reports, 
            "success_count": len(success_reports), 
            "ignored_count": ignored_count
        }
    )

@app.get("/employee/{emp_id}/edit_data")
def get_edit_data(emp_id: int):
    """Возвращает данные сотрудника и списки всех проектов/локаций для формы редактирования"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Основные данные карточки
    cursor.execute("SELECT id, second_name, first_name, curname, phone, status FROM Person WHERE id = ?;", (emp_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"error": "Employee not found"}
    person = dict(row)
    
    # Какие проекты сейчас привязаны к сотруднику
    cursor.execute("SELECT id_pr FROM Project_Person WHERE id_per = ?;", (emp_id,))
    person['selected_projects'] = [r['id_pr'] for r in cursor.fetchall()]
    
    # Какие локации сейчас привязаны к сотруднику
    cursor.execute("SELECT id_pl FROM Place_Person WHERE id_per = ?;", (emp_id,))
    person['selected_places'] = [r['id_pl'] for r in cursor.fetchall()]
    
    # Полный список ВСЕХ проектов из базы данных для чекбоксов
    cursor.execute("SELECT id, name FROM Project;")
    all_projects = [dict(r) for r in cursor.fetchall()]
    
    conn.close()
    return {"person": person, "all_projects": all_projects}


@app.get("/check_duplicate")
def check_duplicate(fio: str):
    """Фоновая валидация существования сотрудника по полному ФИО (без учета регистра)"""
    try:
        parts = [p.strip() for p in fio.split() if p.strip()]
        if len(parts) < 2: 
            return {"exists": False}
            
        second_name = parts[0].capitalize()
        first_name = parts[1].capitalize()
        curname = parts[2].capitalize() if len(parts) > 2 else ""
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Защита: ищем строгое совпадение по ФИО, приведенному к единому регистру
        cursor.execute('''
            SELECT id, second_name, first_name, curname, phone, birthday FROM Person 
            WHERE second_name = ? AND first_name = ? AND curname = ?;
        ''', (second_name, first_name, curname))
        
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"exists": False}
            
        person = dict(row)
        
        # Подтягиваем текущие проекты для вывода в предупреждении на фронтенде
        cursor.execute('''
            SELECT pr.name FROM Project pr 
            JOIN Project_Person pp ON pr.id = pp.id_pr 
            WHERE pp.id_per = ?;
        ''', (person['id'],))
        projects = [r['name'] for r in cursor.fetchall()]
        conn.close()
        
        proj_str = ", ".join(projects) if projects else "Не привязан к проектам"
        details = (
            f"ФИО: {person['second_name']} {person['first_name']} {person['curname']}\n"
            f"Дата рождения: {person['birthday'] or '—'}\n"
            f"Телефон: {person['phone'] or '—'}\n"
            f"Проекты: {proj_str}"
        )
        return {"exists": True, "details": details}
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
    """Обновление данных сотрудника в ручном режиме из карточки редактирования"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        cleaned_phone = re.sub(r'\D', '', phone) if phone else ""
        cursor.execute("UPDATE Person SET status = ?, phone = ? WHERE id = ?;", (status, cleaned_phone, emp_id))
        
        # Обновляем локации: каскадно пересоздаем связи
        cursor.execute("DELETE FROM Place_Person WHERE id_per = ?;", (emp_id,))
        for place_name in places:
            cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
            res = cursor.fetchone()
            place_id = res[0] if res else None
            if not place_id:
                cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                place_id = cursor.lastrowid
            cursor.execute("INSERT INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, emp_id))
            
        # Обновляем проекты: каскадно пересоздаем связи
        cursor.execute("DELETE FROM Project_Person WHERE id_per = ?;", (emp_id,))
        for proj_id in projects: 
            cursor.execute("INSERT INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, emp_id))
            
        # Пересчитываем аккаунты TouchPoint (звонилки) без дублирования префиксов
        cursor.execute("DELETE FROM TouchPoint WHERE id_per = ?;", (emp_id,))
        cursor.execute("SELECT login_ad FROM Person WHERE id = ?;", (emp_id,))
        login_row = cursor.fetchone()
        login_ad = login_row[0] if login_row else None
        
        if login_ad:
            created_suffixes = set()
            for proj_id in projects:
                cursor.execute("SELECT name FROM Project WHERE id = ?;", (proj_id,))
                proj_row = cursor.fetchone()
                if not proj_row: continue
                proj_name = proj_row[0]
                
                p_upper = proj_name.upper()
                suffix = "_unic" if "UNICREDIT" in p_upper else ("_mrg" if "MRG" in p_upper else ("_ozon" if "OZON" in p_upper else ""))
                if suffix and suffix not in created_suffixes:
                    tp_login = make_touchpoint_login(login_ad, proj_name)
                    if tp_login: 
                        cursor.execute("INSERT OR IGNORE INTO TouchPoint (id_pr, id_per, name) VALUES (?, ?, ?);", (proj_id, emp_id, tp_login))
                    created_suffixes.add(suffix)
                    
        conn.commit()
        conn.close()
        
        if current_search: 
            return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
        return RedirectResponse(url="/", status_code=303)
    except Exception as e: 
        if 'conn' in locals(): conn.close()
        return HTMLResponse(content=f"<h3>Ошибка обновления: {e}</h3>", status_code=400)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)

