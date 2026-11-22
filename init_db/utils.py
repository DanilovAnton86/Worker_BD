import datetime
import re
import secrets
import string
import warnings

import pandas as pd


def read_excel_quietly(source, **kwargs):
    """Читает Excel и убирает одно известное безобидное предупреждение.

    openpyxl ругается на проверки данных внутри файла Excel. Если их не
    показывать, на пароли и имена это не влияет. Другие предупреждения
    остаются видимыми.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Data Validation extension is not supported",
            category=UserWarning,
        )
        return pd.read_excel(source, **kwargs)


def sanitize_value(val) -> str:
    """Универсальная защита от NaN, None, 'nan', 'None' и пустот из Excel"""
    if val is None:
        return ""

    # Проверка на pandas NaN
    if pd.isna(val):
        return ""

    # Если пришло число с плавающей точкой (например, телефон 9048280519.0)
    if isinstance(val, float) and val.is_integer():
        val = int(val)

    val_str = str(val).strip()
    if val_str.lower() in ['nan', 'none', 'null', 'nat', '']:
        return ""
        
    return val_str

def normalize_name(name: str) -> str:
    """Приводит слово к виду: первая заглавная, остальные строчные."""
    name = sanitize_value(name)
    if not name: return ""
    name = re.sub(r'\s+', '', name.strip())
    return name.capitalize()

def normalize_login_part(name: str) -> str:
    """Приводит слово к виду для логина: только латиница, все строчные."""
    name = sanitize_value(name)
    if not name: return ""
    return transliterate(name).lower()

def transliterate(text: str) -> str:
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
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password) and 
            any(c.isupper() for c in password) and 
            any(c.isdigit() for c in password)):
            return password

def parse_fio_and_make_login(
    fio_str: str, f_len: int = 1, c_len: int = 1
) -> tuple[str, str, str, str]:
    fio_str = sanitize_value(fio_str)
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
    p_upper = sanitize_value(project_name).upper()
    domain = "@cross.sfcs.cc"
    if "UNICREDIT" in p_upper:
        return f"{login_ad}_unic{domain}"
    elif "MRG" in p_upper:
        return f"{login_ad}_mrg{domain}"
    elif "OZON" in p_upper:
        return f"{login_ad}_ozon{domain}"
    return ""

def clean_phone_number(phone_str) -> str:
    phone_str = sanitize_value(phone_str)
    if not phone_str: return ""
    match = re.search(r'\d{10,11}', phone_str)
    if match: return match.group(0)
    return "".join(filter(str.isdigit, phone_str))

def clean_birthday(date_str):
    """Приводит дату рождения к виду ГГГГ-ММ-ДД.

    Понимает обычные даты (03.03.1979, 1979-03-03) и числа из Excel,
    которые хранят дату как количество дней с 30 декабря 1899 года.
    """
    date_str = sanitize_value(date_str)
    if not date_str:
        return None

    # В Excel к дате иногда прилипает время — отрезаем его.
    if " " in date_str:
        date_str = date_str.split()[0]

    # Серийный номер Excel: например, 28942 вместо 03.03.1979.
    if date_str.isdigit() and len(date_str) > 4:
        try:
            excel_date = datetime.datetime(1899, 12, 30) + datetime.timedelta(
                days=int(date_str)
            )
            return excel_date.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return None

    if date_str.isdigit() and len(date_str) <= 2:
        return None

    date_str = date_str.replace("/", ".").replace("-", ".").replace(",", ".")
    parts = date_str.split(".")

    if len(parts) == 3:
        try:
            if len(parts[0]) == 4:  # ГГГГ.ММ.ДД
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            else:  # ДД.ММ.ГГГГ
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                if len(parts[2]) == 2:
                    year += 1900 if year > 30 else 2000

            current_year = datetime.date.today().year
            if 1900 <= year <= current_year:
                valid_date = datetime.date(year, month, day)
                return valid_date.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None