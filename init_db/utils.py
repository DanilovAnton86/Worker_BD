import secrets
import string
import re
import datetime

def clean_birthday(date_str):
    """Валидирует дату рождения и возвращает строку YYYY-MM-DD или None"""
    if not date_str or str(date_str).strip() == "" or str(date_str).lower() == 'nan':
        return None

    # Если pandas передал объект даты
    if isinstance(date_str, (datetime.date, datetime.datetime)):
        year = date_str.year
        if 1900 <= year <= datetime.date.today().year:
            return date_str.strftime('%Y-%m-%d')
        return None

    date_str = str(date_str).strip()
    if ' ' in date_str:
        date_str = date_str.split()[0] # Отрезаем время, если прилипло

    # Если пришел просто возраст (1-2 цифры), мы его игнорируем (вернем None)
    if date_str.isdigit() and len(date_str) <= 2:
        return None

    # Заменяем все разделители на точки
    date_str = date_str.replace('/', '.').replace('-', '.').replace(',', '.')
    parts = date_str.split('.')

    if len(parts) == 3:
        try:
            if len(parts[0]) == 4: # Формат ГГГГ.ММ.ДД
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            else: # Формат ДД.ММ.ГГГГ
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                # Если год двузначный (86 -> 1986, 05 -> 2005)
                if len(parts[2]) == 2:
                    year += 1900 if year > 30 else 2000

            # Защита от далекого прошлого и будущего (от 1900 до текущего года)
            current_year = datetime.date.today().year
            if 1900 <= year <= current_year:
                # Проверяем, существует ли такая дата в календаре (нет 31 февраля)
                valid_date = datetime.date(year, month, day)
                return valid_date.strftime('%Y-%m-%d')
        except ValueError:
            return None # Если дата неверная (например, 45 месяц)
            
    return None

def transliterate(text: str) -> str:
    """Транслитерация кириллицы в латиницу по стандарту ИКАО"""
    cyrillic = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'iu', 'я': 'ia'
    }
    result = []
    for char in text.lower():
        result.append(cyrillic.get(char, char))
    return "".join(result)

def generate_password(length: int = 12) -> str:
    """Генерация безопасного пароля"""
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password) and 
            any(c.isupper() for c in password) and 
            any(c.isdigit() for c in password)):
            return password

def normalize_name(name: str) -> str:
    """Приводит слово к виду: первая заглавная, остальные строчные. Убирает все лишние пробелы."""
    if not name:
        return ""
    # Убираем любые пробелы (внутри слова тоже, на всякий случай)
    name = re.sub(r'\s+', '', name.strip())
    return name.capitalize()

def normalize_login_part(name: str) -> str:
    """Приводит слово к виду для логина: только латиница, все строчные."""
    if not name:
        return ""
    name = re.sub(r'\s+', '', name.strip())
    return transliterate(name).lower()

def parse_fio_and_make_login(fio_str: str, f_len: int = 1, c_len: int = 1) -> tuple[str, str, str, str]:
    """Разбивает ФИО на части и генерирует уникальный латинский логин AD"""
    # split() без аргументов автоматически обрабатывает множественные пробелы
    parts = fio_str.split()
    
    s_name = normalize_name(parts[0]) if len(parts) > 0 else ""
    f_name = normalize_name(parts[1]) if len(parts) > 1 else ""
    c_name = normalize_name(parts[2]) if len(parts) > 2 else ""
    
    s_trans = normalize_login_part(s_name)
    f_trans = normalize_login_part(f_name)
    c_trans = normalize_login_part(c_name)
    
    f_part = f_trans[:f_len]
    c_part = c_trans[:c_len] if c_trans else ""
    
    if c_part:
        login_ad = f"{f_part}.{c_part}.{s_trans}"
    else:
        login_ad = f"{f_part}.{s_trans}"
        
    return s_name, f_name, c_name, login_ad.lower()

def make_touchpoint_login(login_ad: str, project_name: str) -> str:
    p_upper = project_name.upper()
    domain = "@cross.sfcs.cc"
    if "UNICREDIT" in p_upper:
        return f"{login_ad}_unic{domain}"
    elif "MRG" in p_upper:
        return f"{login_ad}_mrg{domain}"
    elif "OZON" in p_upper:
        return f"{login_ad}_ozon{domain}"
    return ""

import datetime

def clean_birthday(date_str):
    """Валидирует дату рождения и возвращает строку YYYY-MM-DD или None"""
    if not date_str or str(date_str).strip() == "" or str(date_str).lower() == 'nan':
        return None

    # Если pandas передал объект даты
    if isinstance(date_str, (datetime.date, datetime.datetime)):
        year = date_str.year
        if 1900 <= year <= datetime.date.today().year:
            return date_str.strftime('%Y-%m-%d')
        return None

    date_str = str(date_str).strip()
    if ' ' in date_str:
        date_str = date_str.split()[0] # Отрезаем время, если прилипло

    # Если пришел просто возраст (1-2 цифры), мы его игнорируем (вернем None)
    if date_str.isdigit() and len(date_str) <= 2:
        return None

    # Заменяем все разделители на точки
    date_str = date_str.replace('/', '.').replace('-', '.').replace(',', '.')
    parts = date_str.split('.')

    if len(parts) == 3:
        try:
            if len(parts[0]) == 4: # Формат ГГГГ.ММ.ДД
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            else: # Формат ДД.ММ.ГГГГ
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                # Если год двузначный (86 -> 1986, 05 -> 2005)
                if len(parts[2]) == 2:
                    year += 1900 if year > 30 else 2000

            # Защита от далекого прошлого и будущего (от 1900 до текущего года)
            current_year = datetime.date.today().year
            if 1900 <= year <= current_year:
                # Проверяем, существует ли такая дата в календаре (нет 31 февраля)
                valid_date = datetime.date(year, month, day)
                return valid_date.strftime('%Y-%m-%d')
        except ValueError:
            return None # Если дата неверная (например, 45 месяц)
            
    return None