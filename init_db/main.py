import os
import sqlite3
import re
import pandas as pd
import io
from fastapi import UploadFile, File
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Form
from fastapi.responses import RedirectResponse
from fastapi import Query
from utils import generate_password, parse_fio_and_make_login, make_touchpoint_login

app = FastAPI(title="Worker Database System")

# Жесткий список проектов для рендеринга в форме
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

def search_employees_advanced(query: str, status_filter: str, place_filter: list[str], project_filter: str, page: int, limit_val: str, sort_dir: str = "asc"):
    if not os.path.exists(DB_PATH):
        return [], 0
        
    conn = sqlite3.connect(DB_PATH)
    # НАШ СЕКРЕТНЫЙ РЫЧАГ: Учим SQLite переводить русский язык в нижний регистр силами Python!
    conn.create_function("LOWER_RU", 1, lambda x: str(x).lower() if x is not None else "")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    sql_base = "FROM Person p"
    conditions = []
    params = []
    
    if place_filter and len(place_filter) > 0:
        sql_base += " JOIN Place_Person pp ON p.id = pp.id_per JOIN Place pl ON pp.id_pl = pl.id"
        extended_places = list(place_filter)
        if 'Дом' in place_filter and 'УРМ' not in extended_places: extended_places.append('УРМ')
        if 'УРМ' in place_filter and 'Дом' not in extended_places: extended_places.append('Дом')
            
        place_conds = " OR ".join("pl.name = ?" for _ in extended_places)
        conditions.append(f"({place_conds})")
        params.extend(extended_places)
        
    if project_filter and project_filter != 'all':
        sql_base += " JOIN Project_Person prp ON p.id = prp.id_per JOIN Project pr ON prp.id_pr = pr.id"
        conditions.append("pr.name = ?")
        params.append(project_filter)
        
    # ИДЕАЛЬНЫЙ ПОИСК ПО ВХОЖДЕНИЮ С КИРИЛЛИЦЕЙ:
    # Используем LOWER_RU для поиска любой части слова без привязки к регистру и заглавным буквам
    if query:
        conditions.append("(LOWER_RU(p.second_name) LIKE ? OR LOWER_RU(p.login_ad) LIKE ?)")
        query_lower = query.strip().lower()
        params.extend([f"%{query_lower}%", f"%{query_lower}%"])
        
    if status_filter and status_filter != 'all':
        conditions.append("p.status = ?")
        params.append(int(status_filter))
        
    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
        
    # 1. Подсчет общего количества (Исправлено: строго res_count[0] для извлечения числа)
    count_query = f"SELECT COUNT(DISTINCT p.id) {sql_base} {where_clause};"
    cursor.execute(count_query, tuple(params))
    res_count = cursor.fetchone()
    total_found = res_count[0] if res_count else 0
    
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
    order_clause = f" ORDER BY p.second_name {direction}, p.first_name {direction}"
    
    sql_select = f"SELECT DISTINCT p.id, p.first_name, p.second_name, p.curname, p.login_ad, p.password_ad, p.phone, p.birthday, p.email_telescope, p.status {sql_base} {where_clause} {order_clause}"
    
    if limit_val and limit_val != 'all':
        limit = int(limit_val)
        offset = (page - 1) * limit
        sql_select += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
    sql_select += ";"
    
    cursor.execute(sql_select, tuple(params))
    persons = [dict(row) for row in cursor.fetchall()]
    
    for person in persons:
        per_id = person['id']
        cursor.execute("SELECT p.name FROM Place p JOIN Place_Person pp ON p.id = pp.id_pl WHERE pp.id_per = ?;", (per_id,))
        person['places'] = [r['name'] for r in cursor.fetchall()]
        
        cursor.execute("SELECT pr.name FROM Project pr JOIN Project_Person pp ON pr.id = pp.id_pr WHERE pp.id_per = ?;", (per_id,))
        person['projects'] = [r['name'] for r in cursor.fetchall()]
        
    conn.close()
    return persons, total_found

def get_employee_count():
    """Подсчет общего количества людей в базе"""
    if not os.path.exists(DB_PATH):
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Person;")
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0


@app.get("/employee/{emp_id}")
def get_employee_details(emp_id: int):
    """Возвращает абсолютно все данные сотрудника по ID для модального окна"""
    if not os.path.exists(DB_PATH):
        return {"error": "Database not found"}
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Получаем основные данные
    cursor.execute('''
        SELECT id, first_name, second_name, curname, phone, birthday, 
               login_ad, password_ad, email_telescope, email_cross, password_email, status 
        FROM Person WHERE id = ?;
    ''', (emp_id,))
    
    person_row = cursor.fetchone()
    if not person_row:
        conn.close()
        return {"error": "Employee not found"}
        
    person = dict(person_row)
    
    # 2. Получаем локации
    cursor.execute('''
        SELECT p.name FROM Place p
        JOIN Place_Person pp ON p.id = pp.id_pl
        WHERE pp.id_per = ?;
    ''', (emp_id,))
    person['places'] = [r['name'] for r in cursor.fetchall()]
    
    # 3. Получаем проекты
    cursor.execute('''
        SELECT pr.name FROM Project pr
        JOIN Project_Person pp ON pr.id = pp.id_pr
        WHERE pp.id_per = ?;
    ''', (emp_id,))
    person['projects'] = [r['name'] for r in cursor.fetchall()]
    
    # 4. Получаем аккаунты TouchPoint
    cursor.execute('SELECT name FROM TouchPoint WHERE id_per = ?;', (emp_id,))
    person['touchpoints'] = [r['name'] for r in cursor.fetchall()]
    
    conn.close()
    return person

@app.delete("/employee/{emp_id}")
def delete_employee(emp_id: int):
    """Безвозвратное каскадное удаление сотрудника из всей БД"""
    if not os.path.exists(DB_PATH):
        return {"status": "error", "error": "Database file not found"}
        
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # ОБЯЗАТЕЛЬНО включаем поддержку внешних ключей для работы ON DELETE CASCADE
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # Удаляем только из Person, остальное удалится само каскадом
        cursor.execute("DELETE FROM Person WHERE id = ?;", (emp_id,))
        conn.commit()
        
        # Проверяем, удалилась ли запись
        if cursor.rowcount == 0:
            conn.close()
            return {"status": "error", "error": "Сотрудник не найден в базе"}
            
        conn.close()
        return {"status": "success"}
        
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Главная страница: передаем пустые дефолтные фильтры, чтобы Jinja2 не падал"""
    count = get_employee_count()
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "total_count": count, 
            "results": [],             # При первом входе таблица пустая
            "search_query": "",        # Пустой поиск
            "status_filter": "all",    # Дефолтный статус
            "place_filter": [],        # Пустые чекбоксы локаций
            "project_filter": "all",   # Дефолтный проект
            "current_page": 1,         # Первая страница
            "limit": 50,               # Дефолтный лимит строк
            "total_pages": 1,
            "project_list": PROJECTS_LIST
        }
    )

@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request, 
    query: str = Query(None),
    status_filter: str = Query("all"),
    place_filter: list[str] = Query([]),
    project_filter: str = Query("all"),
    page: int = Query(1),
    limit: str = Query("50"),
    sort_dir: str = Query("asc")
):
    search_query_clean = query.strip() if query else ""
    
    # Запускаем продвинутую фильтрацию
    results, total_found = search_employees_advanced(
        search_query_clean, status_filter, place_filter, project_filter, page, limit, sort_dir
    )
    
    # Исправление деления: вытаскиваем число из кортежа total_found, если он пришел в таком виде
    actual_total = total_found[0] if isinstance(total_found, (tuple, list)) else total_found
    
    import math
    if limit == 'all':
        total_pages = 1
        limit_numeric = 0
    else:
        limit_numeric = int(limit)
        total_pages = math.ceil(actual_total / limit_numeric) if actual_total > 0 else 1
        
    count = get_employee_count()
    
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "total_count": count,
            "results": results,
            "search_query": search_query_clean,
            "status_filter": status_filter,
            "place_filter": place_filter,
            "project_filter": project_filter,
            "current_page": page,
            "limit": limit_numeric if limit != 'all' else 'all',
            "total_pages": total_pages,
            "sort_dir": sort_dir,
            "project_list": PROJECTS_LIST
        }
    )

@app.post("/add_user")
def add_user(
    request: Request,
    fio: str = Form(...),
    phone: str = Form(None),
    birthday: str = Form(None),
    places: list[str] = Form([]),
    projects: list[str] = Form([]),
    current_search: str = Form(""),
    is_lookalike: str = Form("false")  # Поймали флаг из вашего скрытого поля формы
):
    try:
        # Валидация телефона
        cleaned_phone = ""
        if phone:
            has_plus = phone.strip().startswith('+')
            digits = re.sub(r'\D', '', phone)
            cleaned_phone = f"+{digits}" if has_plus else digits
            cleaned_phone = cleaned_phone[:16] if has_plus else cleaned_phone[:15]

        # Валидация даты рождения
        formatted_birthday = birthday
        if birthday and '-' in birthday:
            parts = birthday.split('-')
            if len(parts) == 3:
                formatted_birthday = f"{parts[2]}.{parts[1]}.{parts[0]}"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Инициализируем длину срезов для Имени и Отчества
        f_len = 1
        c_len = 1
        
        while True:
            # Генерируем ФИО и логин с текущей длиной букв
            second_name, first_name, curname, login_ad = parse_fio_and_make_login(fio, f_len=f_len, c_len=c_len)
            
            email_telescope = f"{login_ad}@telescope.cc"
            email_cross = f"{login_ad}@cross.cc"
            
            # Проверяем комплексный конфликт по уникальным полям
            cursor.execute('''
                SELECT id FROM Person 
                WHERE login_ad = ? OR email_telescope = ? OR email_cross = ?;
            ''', (login_ad, email_telescope, email_cross))
            
            if not cursor.fetchone():
                break  # Идеально! Логин и почты полностью уникальны, выходим из цикла!
            
            # ЗАЩИТА: Если база нашла дубликат, но менеджер НЕ подтверждал тёлку (is_lookalike пришел "false"),
            # значит произошел тихий повторный клик или сбой JS — блокируем запись.
            if is_lookalike.lower() != "true":
                conn.close()
                return HTMLResponse(content="<h3>Ошибка: Такой сотрудник или логин уже существует. Проверьте данные.</h3>", status_code=400)

            # Если менеджер нажал ОК — плавно разворачиваем строки по ОДНОЙ букве
            from utils import transliterate
            max_f_len = len(transliterate(first_name))
            max_c_len = len(transliterate(curname)) if curname else 0
            
            if f_len < max_f_len:
                f_len += 1  # Добавляем букву к Имени (i -> iv -> iva -> ivan)
            elif max_c_len and c_len < max_c_len:
                c_len += 1  # Если имя полностью развернуто — добавляем буквы к Отчетсву
            else:
                # Аварийный случай: если развернули всё до конца, а в базе сидит абсолютный клон (все буквы совпали)
                conn.close()
                return HTMLResponse(content="<h3>Ошибка: Не удалось создать уникальный буквенный логин. Полный дубликат учетных данных.</h3>", status_code=400)

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
        print(f"Ошибка при добавлении: {e}")
        return HTMLResponse(content=f"<h3>Ошибка базы данных: {e}</h3>", status_code=400)
        
    if current_search:
        return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


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
            if len(parts) < 2: continue # Если нет Фамилии и Имени — шлём в пень
                
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
            
        birthday_col = None
        for col in ['день рождения', 'дата рождения', 'др', 'рождение', 'birthday']:
            if col in df.columns:
                birthday_col = col
                break

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
            if phone_val.lower() in ['nan', 'none']: phone_val = ""
            if phone_val.endswith('.0'): phone_val = phone_val[:-2]
            
            birthday_val = ""
            if birthday_col:
                birthday_val = str(row.get(birthday_col, '')).strip()
                if birthday_val.lower() in ['nan', 'none']: birthday_val = ""
                if ' ' in birthday_val: birthday_val = birthday_val.split()[0]
                if '-' in birthday_val: birthday_val = ".".join(birthday_val.split('-')[::-1])

            proj_val = str(row.get('проект', row.get('project', ''))).strip()
            place_val = str(row.get('локация', row.get('location', ''))).strip()
            
            row_projects = [proj_val] if proj_val and proj_val.lower() != 'nan' else ([default_project] if default_project else [])
            
            row_places = []
            if place_val and place_val.lower() != 'nan':
                if place_val.upper() in ['УРМ', 'ДОМ']: row_places = ['Дом']
                elif place_val.upper() == 'ОФИС': row_places = ['Офис']
                else: row_places = [place_val]
            else:
                row_places = default_places

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

    # 3. АНАЛИЗ ТЁЗОК И ДУБЛИКАТОВ ПО ВАШИМ ТРЕМ СИТУАЦИЯМ
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    for person in parsed_rows:
        # Тянем из базы всех, у кого совпадают только Фамилия и Имя
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
            
            # Ситуация 1: Имя и Фамилия совпали, но ОТЧЕСТВА РАЗНЫЕ (Игорь Николаевич и Игорь Олегович)
            # Системе ничего не угрожает, пропускаем проверку (is_duplicate остается False)
            if db_curname and file_curname and db_curname != file_curname:
                continue
                
            # Ситуация 3: ПОЛНЫЕ ТЁЗКИ (Отчества полностью совпали)
            elif db_curname == file_curname:
                person['is_duplicate'] = True
                person['db_match_details'] = f"Полный тёзка в БД: {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                break # Нашли критическое совпадение, выходим из проверки этой строки
                
            # Ситуация 2: У кого-то одного (в базе или файле) ОТЧЕСТВО ПУСТОЕ, а у второго заполнено
            # (Например: Якубович Виталий и Якубович Виталий Вячеславович) -> Выдаем подозрение на дубликат
            elif not db_curname or not file_curname:
                person['is_duplicate'] = True
                person['db_match_details'] = f"Подозрение на дубликат (нет отчества): {m['second_name']} {m['first_name']} {db_curname} (ДР: {m['birthday'] or '—'}, Тел: {m['phone'] or '—'})"
                break
                
    conn.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="import_preview.html", 
        context={"rows": parsed_rows, "total_count": len(parsed_rows)}
    )

@app.post("/enrich_file")
async def enrich_file(request: Request, file: UploadFile = File(...)):
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents), dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    has_fio = "фио" in df.columns
    has_split_name = "фамилия" in df.columns
    if not (has_fio or has_split_name):
        return HTMLResponse(content="<h3>Ошибка: В файле должны быть колонки 'ФИО' или 'Фамилия'</h3>", status_code=400)
        
    birthday_col = None
    for col in ['день рождения', 'дата рождения', 'др', 'рождение', 'birthday']:
        if col in df.columns:
            birthday_col = col
            break

    enrich_rows = []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    for _, row in df.iterrows():
        if has_split_name:
            s_name = str(row.get('фамилия', '')).strip().capitalize()
            f_name = str(row.get('имя', '')).strip().capitalize()
            c_name = str(row.get('отчество', '')).strip().capitalize()
            if f_name.lower() in ['nan', 'none', '']: f_name = ""
            if c_name.lower() in ['nan', 'none', '']: c_name = ""
        else:
            fio_str = str(row.get('фио', '')).strip()
            fio_parts = [p.strip() for p in fio_str.split() if p.strip()]
            if len(fio_parts) < 1: continue
            s_name = fio_parts[0].capitalize()
            f_name = fio_parts[1].capitalize() if len(fio_parts) > 1 else ""
            c_name = fio_parts[2].capitalize() if len(fio_parts) > 2 else ""

        if not s_name or s_name == 'Nan':
            continue

        new_phone = str(row.get('телефон', row.get('phone', ''))).strip()
        if new_phone.lower() in ['nan', 'none']: new_phone = ""
        if new_phone.endswith('.0'): new_phone = new_phone[:-2]
        if new_phone:
            has_plus = new_phone.startswith('+')
            digits = re.sub(r'\D', '', new_phone)
            new_phone = f"+{digits}" if has_plus else digits
            new_phone = new_phone[:16] if has_plus else new_phone[:15]

        new_birthday = ""
        if birthday_col:
            raw_bday = str(row.get(birthday_col, '')).strip()
            if raw_bday.lower() not in ['nan', 'none', '']:
                if ' ' in raw_bday:
                    raw_bday = raw_bday.split()[0]
                raw_bday = raw_bday.replace('-', '.').replace(',', '.')
                raw_bday = "".join(ch for ch in raw_bday if ch in "0123456789.")
                dp = raw_bday.split('.')
                if len(dp) == 3:
                    if len(dp[0]) == 4:
                        new_birthday = f"{dp[2]}.{dp[1]}.{dp[0]}"
                    else:
                        new_birthday = raw_bday[:10]
                else:
                    new_birthday = raw_bday[:10]

        if not new_phone and not new_birthday:
            continue

        if f_name:
            cursor.execute("SELECT id, first_name, second_name, curname, phone, birthday FROM Person WHERE second_name = ? AND first_name = ?;", (s_name, f_name))
        else:
            cursor.execute("SELECT id, first_name, second_name, curname, phone, birthday FROM Person WHERE second_name = ?;", (s_name,))
            
        db_matches = [dict(r) for r in cursor.fetchall()]

        for db_user in db_matches:
            db_id = db_user['id']
            db_first_name = db_user['first_name'] or ""
            db_curname = db_user['curname'] or ""
            db_phone = db_user['phone'] or ""
            db_birthday_raw = db_user['birthday'] or ""
            
            db_birthday = ""
            if db_birthday_raw:
                if ' ' in db_birthday_raw:
                    db_birthday_raw = db_birthday_raw.split()[0]
                db_birthday_raw = db_birthday_raw.replace('-', '.').replace(',', '.')
                db_birthday_raw = "".join(ch for ch in db_birthday_raw if ch in "0123456789.")
                dp = db_birthday_raw.split('.')
                if len(dp) == 3 and len(dp[0]) == 4:
                    db_birthday = f"{dp[2]}.{dp[1]}.{dp[0]}"
                else:
                    db_birthday = db_birthday_raw[:10]

            status_type = "normal" 
            info_msg = ""
            auto_check = "checked"

            if not f_name and db_first_name:
                status_type = "partial_mismatch"
                info_msg = f"❓ Похожая запись по Фамилии (В базе: {db_first_name} {db_curname}, имя не указано)"
                auto_check = ""
            elif db_curname and c_name and db_curname != c_name:
                status_type = "partial_mismatch"
                info_msg = f"❓ Похожая запись, но разные отчества (В базе: {db_curname}, в файле: {c_name})"
                auto_check = ""
            elif (not db_curname and c_name) or (db_curname and not c_name):
                status_type = "partial_empty"
                info_msg = f"❓ Похожая запись (В базе: {db_curname or 'нет отчества'}, в файле: {c_name or 'нет отчества'})"
                auto_check = ""
            else:
                is_phone_changed = new_phone and db_phone and db_phone != new_phone
                is_birthday_changed = new_birthday and db_birthday and db_birthday != new_birthday

                if is_phone_changed or is_birthday_changed:
                    status_type = "conflict"
                    # ТЕПЕРЬ ТУТ ЧИСТЫЙ ТЕКСТ ДЛЯ ЯЧЕЙКИ ТАБЛИЦЫ, БЕЗ СИСТЕМНОГО МУСОРА
                    info_msg = "⚠️ Различаются телефон или дата рождения"
                else:
                    if (new_phone == db_phone or not new_phone) and (new_birthday == db_birthday or not new_birthday):
                        continue
                    status_type = "auto"
                    info_msg = "✅ Данные будут дополнены"

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

    conn.close()
    return templates.TemplateResponse(request=request, name="enrich_preview.html", context={"rows": enrich_rows, "total_count": len(enrich_rows)})

@app.post("/confirm_import", response_class=HTMLResponse)
def confirm_import(request: Request, import_indexes: list[int] = Form([])):
    if not os.path.exists(DB_PATH): 
        return HTMLResponse(content="<h3>Ошибка: База данных не найдена</h3>", status_code=400)
        
    form_data = request._form
    success_reports = []
    ignored_count = 0
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # Считаем общее число строк в пришедшей форме по скрытым полям Фамилии
        total_rows_in_form = sum(1 for key in form_data.keys() if key.startswith('second_name_'))
        
        for idx in range(total_rows_in_form):
            # Если менеджер снял галочку с этой строки — пропускаем её
            if idx not in import_indexes:
                ignored_count += 1
                continue
                
            # Достаем текстовые данные текущей строки
            second_name = form_data.get(f"second_name_{idx}", "").strip()
            first_name = form_data.get(f"first_name_{idx}", "").strip()
            curname = form_data.get(f"curname_{idx}", "").strip()
            phone = form_data.get(f"phone_{idx}", "").strip()
            birthday = form_data.get(f"birthday_{idx}", "").strip()
            
            projects = form_data.getlist(f"projects_{idx}")
            # СЧИТЫВАЕМ ИНДИВИДУАЛЬНЫЕ ЧЕКБОКСЫ ЛОКАЦИЙ (placesX) прямо из строки превью
            places = form_data.getlist(f"places{idx}")
            
            # Запускаем посимвольный подбор уникального логина
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
                    
                email_telescope = f"{login_ad}@telescope.cc"
                email_cross = f"{login_ad}@cross.cc"
                
                cursor.execute('''
                    SELECT id FROM Person 
                    WHERE login_ad = ? OR email_telescope = ? OR email_cross = ?;
                ''', (login_ad, email_telescope, email_cross))
                
                if not cursor.fetchone(): 
                    break # Логин и почты свободны!
                    
                if f_len < len(f_name_trans): f_len += 1
                elif c_name_trans and c_len < len(c_name_trans): c_len += 1
                else: digit_tail = "1" if not digit_tail else str(int(digit_tail) + 1)
                
            # Генерируем один общий пароль
            shared_password = generate_password()
            
            # Вставляем человека в Person
            cursor.execute('''
                INSERT INTO Person (
                    first_name, second_name, curname, phone, birthday, 
                    login_ad, password_ad, email_telescope, email_cross, password_email, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
            ''', (first_name, second_name, curname, phone, birthday, 
                  login_ad, shared_password, email_telescope, email_cross, shared_password))
            
            person_id = cursor.lastrowid
            
            # ЗАЩИТА СВЯЗЕЙ (INSERT OR IGNORE): Ошибка UNIQUE constraint failed здесь больше не появится
            # ИСПРАВЛЕНИЕ: Вытаскиваем чистый ID из кортежа [0] для локаций
            for place_name in places:
                if not place_name: continue
                cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
                res = cursor.fetchone()
                place_id = res[0] if res else None # <-- Добавили [0]
                if not place_id:
                    cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                    place_id = cursor.lastrowid
                cursor.execute("INSERT OR IGNORE INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, person_id))
                
            # ИСПРАВЛЕНИЕ: Вытаскиваем чистый ID из кортежа [0] для проектов
            created_suffixes = set() # Объявляем здесь, чтобы не было NameError
            for proj_name in projects:
                if not proj_name: continue
                cursor.execute("SELECT id FROM Project WHERE name = ?;", (proj_name,))
                res = cursor.fetchone()
                proj_id = res[0] if res else None # <-- Добавили [0]
                if not proj_id:
                    cursor.execute("INSERT INTO Project (name) VALUES (?);", (proj_name,))
                    proj_id = cursor.lastrowid
                    
                cursor.execute("INSERT OR IGNORE INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, person_id))
                
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
                        
            success_reports.append({
                "fio": f"{second_name} {first_name} {curname}", 
                "login": login_ad, 
                "password": shared_password
            })
            
        conn.commit()
        conn.close()
        
    except Exception as e:
        if 'conn' in locals(): conn.close()
        print(f"Критическая ошибка импорта: {e}")
        return HTMLResponse(content=f"<h3>Ошибка записи импорта в БД: {e}</h3>", status_code=400)
        
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Основные данные
    cursor.execute("SELECT id, second_name, first_name, curname, phone, status FROM Person WHERE id = ?;", (emp_id,))
    person = dict(cursor.fetchone())
    
    # Какие проекты сейчас выбраны
    cursor.execute("SELECT id_pr FROM Project_Person WHERE id_per = ?;", (emp_id,))
    person['selected_projects'] = [r['id_pr'] for r in cursor.fetchall()]
    
    # Какие локации сейчас выбраны
    cursor.execute("SELECT id_pl FROM Place_Person WHERE id_per = ?;", (emp_id,))
    person['selected_places'] = [r['id_pl'] for r in cursor.fetchall()]
    
    # Список ВСЕХ проектов из базы с их ID для чекбоксов
    cursor.execute("SELECT id, name FROM Project;")
    all_projects = [dict(r) for r in cursor.fetchall()]
    
    conn.close()
    return {"person": person, "all_projects": all_projects}

@app.get("/check_duplicate")
def check_duplicate(fio: str):
    """Фоновая проверка на существование сотрудника по полному ФИО с подтягиванием его проектов"""
    try:
        parts = [p.strip() for p in fio.split() if p.strip()]
        if len(parts) < 2:
            return {"exists": False}
        
        second_name = parts[0].capitalize()
        first_name = parts[1].capitalize()
        curname = parts[2].capitalize() if len(parts) > 2 else "" # Безопасное извлечение отчества
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, second_name, first_name, curname, phone, birthday 
            FROM Person 
            WHERE second_name = ? AND first_name = ? AND curname = ?;
        ''', (second_name, first_name, curname))
        
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"exists": False}
            
        person = dict(row)
        
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

@app.post("/add_user")
def add_user(
    request: Request,
    fio: str = Form(...),
    phone: str = Form(None),
    birthday: str = Form(None),
    places: list[str] = Form([]),
    projects: list[str] = Form([]),
    current_search: str = Form("")
):
    try:
        # 1. Валидация и очистка телефона от мусора
        cleaned_phone = ""
        if phone:
            has_plus = phone.strip().startswith('+')
            digits = re.sub(r'\D', '', phone)
            cleaned_phone = f"+{digits}" if has_plus else digits
            cleaned_phone = cleaned_phone[:16] if has_plus else cleaned_phone[:15]

        # 2. Валидация и форматирование даты рождения
        formatted_birthday = birthday
        if birthday and '-' in birthday:
            parts_date = birthday.split('-')
            if len(parts_date) == 3:
                formatted_birthday = f"{parts_date[2]}.{parts_date[1]}.{parts_date[0]}"

        # 3. Разбиваем ФИО с правильными индексами
        parts_fio = [p.strip() for p in fio.split() if p.strip()]
        if len(parts_fio) < 2:
            return HTMLResponse(content="<h3>Ошибка: ФИО должно содержать Фамилию и Имя</h3>", status_code=400)
            
        second_name = parts_fio[0].capitalize()  # Фамилия
        first_name = parts_fio[1].capitalize()   # Имя
        curname = parts_fio[2].capitalize() if len(parts_fio) > 2 else ""  # Отчество
        
        from utils import transliterate
        s_name_trans = transliterate(second_name)
        f_name_trans = transliterate(first_name)
        c_name_trans = transliterate(curname) if curname else ""

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # 4. АЛГОРИТМ УМНОГО ПОДБОРА ЛОГИНА И ПОЧТЫ (Посимвольное расширение)
        f_len = 1  
        c_len = 1  
        digit_tail = "" 
        
        while True:
            f_part = f_name_trans[:f_len]
            c_part = c_name_trans[:c_len] if c_name_trans else ""
            
            if c_part:
                login_ad = f"{f_part}.{c_part}.{s_name_trans}{digit_tail}"
            else:
                login_ad = f"{f_part}.{s_name_trans}{digit_tail}"
                
            # Генерируем почтовые ящики СТРОГО на основе текущего варианта логина
            email_telescope = f"{login_ad}@telescope.cc"
            email_cross = f"{login_ad}@cross.cc"
                
            # Проверяем комплексный конфликт уникальности по всей таблице Person
            cursor.execute('''
                SELECT id FROM Person 
                WHERE login_ad = ? OR email_telescope = ? OR email_cross = ?;
            ''', (login_ad, email_telescope, email_cross))
            
            if not cursor.fetchone():
                break  # И логин, и обе почты абсолютно свободны! Выходим из цикла
                
            # Если что-то из этого занято — расширяем параметры по одной букве
            if f_len < len(f_name_trans):
                f_len += 1  # Добавляем букву к имени (i -> iv -> iva -> ivan)
            elif c_name_trans and c_len < len(c_name_trans):
                c_len += 1  # Имя кончилось — разворачиваем отчество по буквам
            else:
                # Если развернули все строки, а совпадение 100% — крепим цифру в хвост
                if not digit_tail:
                    digit_tail = "1"
                else:
                    digit_tail = str(int(digit_tail) + 1)

        # 5. Генерируем ОДИН общий пароль для всего
        shared_password = generate_password()

        # 6. Вставляем сотрудника в таблицу Person
        cursor.execute('''
            INSERT INTO Person (
                first_name, second_name, curname, phone, birthday, 
                login_ad, password_ad, email_telescope, email_cross, password_email, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
        ''', (first_name, second_name, curname, cleaned_phone, formatted_birthday, 
              login_ad, shared_password, email_telescope, email_cross, shared_password))
        
        person_id = cursor.lastrowid
        
        # 7. Связываем сотрудника с Локациями (Place)
        for place_name in places:
            cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
            res = cursor.fetchone()
            place_id = res[0] if res else None
            if not place_id:
                cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                place_id = cursor.lastrowid
            cursor.execute("INSERT INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, person_id))
            
        # 8. Связываем сотрудника с Проектами и создаем TouchPoint без дубликатов
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
        print(f"Ошибка при добавлении: {e}")
        return HTMLResponse(content=f"<h3>Ошибка базы данных: {e}</h3>", status_code=400)
        
    if current_search:
        return RedirectResponse(url=f"/search?query={current_search}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@app.post("/employee/{emp_id}/update")
def update_employee(
    emp_id: int,
    status: int = Form(...),
    phone: str = Form(None),
    places: list[str] = Form([]),     # Придут имена локаций (Офис/Дом)
    projects: list[int] = Form([]),    # Придут ID выбранных проектов
    current_search: str = Form("")     # <-- Поймали скрытое поле поиска
):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # 1. Обновляем телефон и статус в таблице Person
        cleaned_phone = re.sub(r'\D', '', phone) if phone else ""
        cursor.execute("UPDATE Person SET status = ?, phone = ? WHERE id = ?;", (status, cleaned_phone, emp_id))
        
        # 2. Обновляем Локации: удаляем старые связи, пишем новые
        cursor.execute("DELETE FROM Place_Person WHERE id_per = ?;", (emp_id,))
        for place_name in places:
            cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
            res = cursor.fetchone()
            place_id = res[0] if res else None
            if not place_id:
                cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                place_id = cursor.lastrowid
            cursor.execute("INSERT INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, emp_id))
            
        # 3. Обновляем Проекты: удаляем старые связи, пишем новые
        cursor.execute("DELETE FROM Project_Person WHERE id_per = ?;", (emp_id,))
        for proj_id in projects:
            cursor.execute("INSERT INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, emp_id))
            
        # 4. Пересчитываем TouchPoint: удаляем старые и генерируем заново без дубликатов
        cursor.execute("DELETE FROM TouchPoint WHERE id_per = ?;", (emp_id,))
        cursor.execute("SELECT login_ad FROM Person WHERE id = ?;", (emp_id,))
        login_ad = cursor.fetchone()[0]
        
        created_suffixes = set()
        for proj_id in projects:
            cursor.execute("SELECT name FROM Project WHERE id = ?;", (proj_id,))
            proj_name = cursor.fetchone()[0]
            
            p_upper = proj_name.upper()
            suffix = ""
            if "UNICREDIT" in p_upper: suffix = "_unic"
            elif "MRG" in p_upper: suffix = "_mrg"
            elif "OZON" in p_upper: suffix = "_ozon"
            
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
        return HTMLResponse(content=f"<h3>Ошибка обновления: {e}</h3>", status_code=400)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
