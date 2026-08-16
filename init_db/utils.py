import random
import string

def generate_password(length=12) -> str:
    """Генерация надежного пароля без похожих символов (l, I, 1, o, O, 0)"""
    # Исключаем: O, o, 0, I, l, 1 и спецсимволы
    uppercase = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
    lowercase = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    
    all_chars = uppercase + lowercase + digits
    
    # Гарантируем наличие хотя бы одной заглавной, строчной и цифры
    password = [
        random.choice(uppercase),
        random.choice(lowercase),
        random.choice(digits)
    ]
    
    # Добираем остальные символы
    password += [random.choice(all_chars) for _ in range(length - 3)]
    random.shuffle(password)
    return "".join(password)

def transliterate(text: str) -> str:
    """Транслитерация ФИО под формат логинов"""
    rules = {
        'a': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ы': 'y', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ъ': '', 'ь': ''
    }
    cleaned = text.lower().strip()
    return "".join(rules.get(char, char) for char in cleaned)

def parse_fio_and_make_login(fio_str: str):
    """Очищает ФИО от лишних пробелов, разбивает на Фамилию, Имя, Отчество и генерирует логин"""
    # Разбиваем по пробелам и убираем пустые элементы
    parts = [p.strip() for p in fio_str.split() if p.strip()]
    
    if len(parts) < 2:
        raise ValueError("ФИО должно содержать как минимум Фамилию и Имя")
        
    second_name = parts[0].capitalize()  # Фамилия (Иванова)
    first_name = parts[1].capitalize()   # Имя (Кульжьян)
    curname = parts[2].capitalize() if len(parts) > 2 else "" # Отчество (Базарбаевна)
    
    # Генерация логина: первая буква имени . первая буква отчества . фамилия
    # Берем первый символ [0] от транслитерированного имени/отчества
    f_letter = transliterate(first_name)[0] if first_name else ""
    c_letter = transliterate(curname)[0] if curname else ""
    s_name_trans = transliterate(second_name)
    
    if c_letter:
        login_ad = f"{f_letter}.{c_letter}.{s_name_trans}"
    else:
        login_ad = f"{f_letter}.{s_name_trans}"
        
    return second_name, first_name, curname, login_ad


def make_touchpoint_login(login_ad: str, project_name: str) -> str:
    """Генерирует логин звонилки TouchPoint на основе проекта"""
    p_upper = project_name.upper()
    
    if "UNICREDIT" in p_upper:
        suffix = "_unic"
    elif "MRG" in p_upper:
        suffix = "_mrg"
    elif "OZON" in p_upper:
        suffix = "_ozon"
    else:
        return "" # Если проект не входит в эти три группы, TouchPoint не создается
        
    return f"{login_ad}{suffix}@cross.sfcs.cc"
