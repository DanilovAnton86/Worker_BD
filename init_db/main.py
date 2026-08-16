import os
import sqlite3
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
    """Роут обработки поиска"""
    results = search_employees(query)
    count = get_employee_count()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "total_count": count,
            "results": results,
            "search_query": query
        }
    )

@app.post("/add_user")
def add_user(
    request: Request,
    fio: str = Form(...),
    phone: str = Form(None),
    birthday: str = Form(None),
    places: list[str] = Form(...),
    projects: list[str] = Form(...)
):
    try:
        # 1. Автоматическая обработка ФИО и генерация логина
        second_name, first_name, curname, login_ad = parse_fio_and_make_login(fio)
        
        # 2. Автоматическая генерация паролей
        password_ad = generate_password()
        password_email = generate_password()
        
        # 3. Генерация почтовых ящиков
        email_telescope = f"{login_ad}@telescope.cc" # Пример вашего домена
        email_cross = f"{login_ad}@cross.cc"
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # 4. Вставляем сотрудника в таблицу Person
        cursor.execute('''
            INSERT INTO Person (
                first_name, second_name, curname, phone, birthday, 
                login_ad, password_ad, email_telescope, email_cross, password_email, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
        ''', (first_name, second_name, curname, phone, birthday, 
              login_ad, password_ad, email_telescope, email_cross, password_email))
        
        person_id = cursor.lastrowid # Получаем ID только что созданного человека
        
        # 5. Связываем сотрудника с Локациями (Place)
        for place_name in places:
            cursor.execute("SELECT id FROM Place WHERE name = ?;", (place_name,))
            res = cursor.fetchone()
            if res:
                place_id = res[0] # Берем первый элемент из кортежа
            else:
                cursor.execute("INSERT INTO Place (name) VALUES (?);", (place_name,))
                place_id = cursor.lastrowid
            
            cursor.execute("INSERT INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_id, person_id))
            
        # 6. Связываем сотрудника с Проектами и создаем TouchPoint
        for proj_name in projects:
            cursor.execute("SELECT id FROM Project WHERE name = ?;", (proj_name,))
            res = cursor.fetchone()
            if res:
                proj_id = res[0] # Берем первый элемент из кортежа
            else:
                cursor.execute("INSERT INTO Project (name) VALUES (?);", (proj_name,))
                proj_id = cursor.lastrowid

                
            # Связь в Project_Person
            cursor.execute("INSERT INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (proj_id, person_id))
            
            # Проверяем, нужно ли для этого проекта сгенерировать TouchPoint аккаунт
            tp_login = make_touchpoint_login(login_ad, proj_name)
            if tp_login:
                # В вашей схеме в TouchPoint имя аккаунта записывается в поле 'name'
                cursor.execute('''
                    INSERT OR IGNORE INTO TouchPoint (id_pr, id_per, name) 
                    VALUES (?, ?, ?);
                ''', (proj_id, person_id, tp_login))
                
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Ошибка при добавлении: {e}")
        # Для простоты возвращаем ошибку текстом, можно красивее оформить
        return HTMLResponse(content=f"<h3>Ошибка базы данных: {e}</h3>", status_code=400)
        
    # После успешного добавления перенаправляем на главную, где обновится счетчик
    return RedirectResponse(url="/", status_code=303)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
