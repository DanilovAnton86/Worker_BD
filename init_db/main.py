import os
import sqlite3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Worker Database System")

# Находим жесткий путь к нашей базе данных и шаблонам
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(CURRENT_DIR, "templates"))
DB_PATH = os.path.join(CURRENT_DIR, 'UZ_OP.db')

def get_employee_count():
    """Безопасный подсчет людей в базе с извлечением чистого числа"""
    if not os.path.exists(DB_PATH):
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Person;")
    res = cursor.fetchone()
    conn.close()
    # res — это кортеж типа (3214,). Берем только первое число [0]
    return res[0] if res else 0

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Главная страница, отдающая HTML с контекстом"""
    count = get_employee_count()
    # В FastAPI ОБЯЗАТЕЛЬНО передавать request внутрь контекста!
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"total_count": count}
    )

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
