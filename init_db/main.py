import os
import sqlite3
import re
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Form
from fastapi.responses import RedirectResponse
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

def search_employees(query: str):
    """Поиск сотрудников по Фамилии или Логину с подтягиванием Проектов и Локаций"""
    if not os.path.exists(DB_PATH):
        return []
    
    conn = sqlite3.connect(DB_PATH)
    # Позволяет получать результаты в виде словарей, а не кортежей
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Ищем людей по Фамилии (LIKE) или по Логину (равенство)
    search_pattern = f"%{query}%"
    cursor.execute('''
        SELECT id, first_name, second_name, curname, login_ad, password_ad, phone, email_telescope, status 
        FROM Person 
        WHERE second_name LIKE ? OR login_ad = ?;
    ''', (search_pattern, query))
    
    persons = [dict(row) for row in cursor.fetchall()]
    
    # Для каждого найденного сотрудника собираем его проекты и локации
    for person in persons:
        per_id = person['id']
        
        # Получаем локации (Офис/Дом)
        cursor.execute('''
            SELECT p.name FROM Place p
            JOIN Place_Person pp ON p.id = pp.id_pl
            WHERE pp.id_per = ?;
        ''', (per_id,))
        person['places'] = [r['name'] for r in cursor.fetchall()]
        
        # Получаем проекты
        cursor.execute('''
            SELECT pr.name FROM Project pr
            JOIN Project_Person pp ON pr.id = pp.id_pr
            WHERE pp.id_per = ?;
        ''', (per_id,))
        person['projects'] = [r['name'] for r in cursor.fetchall()]
        
    conn.close()
    return persons

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
    count = get_employee_count()
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"total_count": count, "project_list": PROJECTS_LIST}
    )

@app.get("/search", response_class=HTMLResponse)
def search(request: Request, query: str):
    results = search_employees(query)
    count = get_employee_count()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "total_count": count,
            "results": results,
            "search_query": query,
            "project_list": PROJECTS_LIST # Теперь список проектов не исчезнет после поиска!
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
