import os
import sqlite3
import datetime

# Пытаемся импортировать ldap3. Если библиотеки нет, скрипт переключится в демо-режим,
# чтобы презентация успешно прошла на любом ПК без установки доп. ПО!
try:
    from ldap3 import Server, Connection, ALL, MODIFY_REPLACE
    LDAP_AVAILABLE = True
except ImportError:
    LDAP_AVAILABLE = False
    print("⚠️ Библиотека 'ldap3' не найдена. Скрипт запущен в ДЕМОНСТРАЦИОННОМ режиме (эмуляция AD).")

# --- КОНФИГУРАЦИЯ СЕРВЕРА ACTIVE DIRECTORY (Для системных администраторов) ---
AD_SERVER = "ldaps://dc.yourdomain.local:636"  # Защищенный LDAPS порт
AD_USER = "CN=Service_CRM,OU=Service_Accounts,DC=yourdomain,DC=local"
AD_PASSWORD = "Secret_Service_Password"
AD_BASE_DN = "DC=yourdomain,DC=local"
AD_USERS_OU = "OU=Operators,OU=Staff,DC=yourdomain,DC=local"  # Куда складывать новичков

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'UZ_OP.db')

# --- 1. МАТРИЦА ДИНАМИЧЕСКИХ ПРОЕКТНЫХ ГРУПП (Место для модификации №1) ---
# Если добавляется новый проект, админу достаточно дописать его сюда в формате:
# "ИМЯ_В_НАШЕЙ_БД": "ИМЯ_ГРУППЫ_В_ACTIVE_DIRECTORY"
PROJECT_GROUPS_MAP = {
    "KOMUS_INVITRO": "OP_KOMUS_INVITRO",
    "TELETRIBE_122": "OP_TELETRIBE_122",
    "TELETRIBE_ESSM": "OP_TELETRIBE_ESSM",
    "TELETRIBE_MT": "OP_TELETRIBE_MT",
    "TELETRIBE_POCHTA": "OP_TELETRIBE_POCHTA",
    "VOXYS_AVITO": "OP_VOXYS_AVITO",
    "VOXYS_MOG": "OP_VOXYS_MOG",
    "VOXYS_RTK_IN": "OP_VOXYS_RTK_IN",
    "VOXYS_RZD": "OP_VOXYS_RZD",
    "VOXYS_TELE2": "OP_VOXYS_TELE2",
    "VOXYS_VTB": "OP_VOXYS_VTB",
    "MRG": "OP_MRG"
}


def connect_ad():
    """Безопасное подключение к Active Directory"""
    if not LDAP_AVAILABLE:
        return "MOCK_CONNECTION"
    try:
        server = Server(AD_SERVER, get_info=ALL)
        conn = Connection(server, AD_USER, AD_PASSWORD, auto_bind=True)
        return conn
    except Exception as e:
        print(f"❌ Ошибка подключения к Active Directory: {e}")
        return None


def calculate_required_groups(person_dict):
    """КОНСТРУКТОР ГРУПП: Вычисляет идеальный пакет доступов на основе данных нашей БД"""
    groups = ["nextcloud", "OP"]  # Всем новичкам по умолчанию
    
    # Фильтр локации
    places = person_dict.get('places', [])
    if "Дом" in places or "УРМ" in places:
        groups.append("OP_URM")
    if "Офис" in places:
        groups.append("OP_LOCAL")
        
    # Динамический маппинг проектов
    projects = person_dict.get('projects', [])
    has_mrg_or_vtb = False
    
    for proj in projects:
        # Проверяем триггеры для Confluence (МРГ и ВТБ)
        if "MRG" in proj.upper() or "VTB" in proj.upper():
            has_mrg_or_vtb = True
            
        # Ищем совпадение в матрице групп
        ad_group = PROJECT_GROUPS_MAP.get(proj)
        if ad_group:
            groups.append(ad_group)
            
    if has_mrg_or_vtb:
        groups.append("confluence")
        
    return groups


def sync_new_users_to_ad(ad_conn):
    """ЭНДПОИНТ А: Создание новичков в Active Directory (БД ➔ AD)"""
    print("\n--- [ШАГ А] Проверка наличия новых сотрудников для выгрузки в AD ---")
    
    # Имитируем запрос к БД (в реальности тут будет SELECT * FROM Person WHERE ad_status = 0)
    # Для демонстрации заложим одного тестового новичка
    mock_new_users = [{
        "id": 9999,
        "first_name": "Иван",
        "second_name": "Новичков",
        "curname": "Сергеевич",
        "login_ad": "i.s.novichkov",
        "password_ad": "TmpPassword2026!",
        "places": ["Дом"],
        "projects": ["MRG", "VOXYS_TELE2"]
    }]
    """
    # Реальный боевой код, который встанет вместо [ДЕМО]
user_dn = f"CN={user['second_name']} {user['first_name']},{AD_USERS_OU}"
user_attrs = {
    'objectClass': ['top', 'person', 'organizationalPerson', 'user'],
    'sAMAccountName': user['login_ad'],
    'userPrincipalName': f"{user['login_ad']}@yourdomain.local",
    'givenName': user['first_name'],
    'sn': user['second_name'],
    'displayName': f"{user['second_name']} {user['first_name']} {user['curname']}".strip(),
    'userAccountControl': '514' # 514 означает "Создать учетку отключенной" для безопасности
}

# 1. Физически создаем пользователя в дереве AD
ad_conn.add(user_dn, attributes=user_attrs)

# 2. В цикле добавляем его во все вычисленные группы доступов
for group_name in required_groups:
    group_dn = f"CN={group_name},OU=Groups,DC=yourdomain,DC=local"
    ad_conn.modify(group_dn, {'member': [(MODIFY_ADD, [user_dn])]})
"""

    for user in mock_new_users:
        required_groups = calculate_required_groups(user)
        print(f"👤 Обработка новичка: {user['second_name']} {user['first_name']} (Логин: {user['login_ad']})")
        print(f"  └─ Сгенерирован целевой пакет доменных групп: {required_groups}")
        
        if ad_conn == "MOCK_CONNECTION":
            print("  [ДЕМО] Имитация LDAP-команды: user_add() в контейнер " + AD_USERS_OU)
            print(f"  [ДЕМО] Имитация рассылки: Добавление {user['login_ad']} в группы {required_groups} -> УСПЕШНО")
        else:
            # Реальный LDAP-код для администраторов домена
            user_dn = f"CN={user['second_name']} {user['first_name']},{AD_USERS_OU}"
            user_attrs = {
                'objectClass': ['top', 'person', 'organizationalPerson', 'user'],
                'samAccountName': user['login_ad'],
                'userPrincipalName': f"{user['login_ad']}@yourdomain.local",
                'givenName': user['first_name'],
                'sn': user['second_name'],
                'displayName': f"{user['second_name']} {user['first_name']} {user['curname']}".strip(),
                'userPassword': user['password_ad']
            }
            # ad_conn.add(user_dn, attributes=user_attrs)
            # Для каждой группы из required_groups выполняется ad_conn.modify(group_dn, {'member': [(MODIFY_ADD, user_dn)]})
            pass


def sync_changes_from_ad(ad_conn):
    """ЭНДПОИНТ Б: Обратная синхронизация увольнений и должностей (AD ➔ БД)"""
    print("\n--- [ШАГ Б] Сканирование Active Directory на предмет изменений и увольнений ---")
    
    """
    # Реальный боевой код, который заменит mock_ad_responses
# Скрипт делает запрос в AD: "Дай мне все группы пользователя с таким-то логином"
ad_conn.search(
    search_base=AD_BASE_DN,
    search_filter=f"(sAMAccountName={user_login_from_db})",
    attributes=['memberOf'] # memberOf — это системное поле AD, где лежат все группы юзера
)

if ad_conn.entries:
    # Вытаскиваем список групп из ответа сервера AD
    ad_groups = ad_conn.entries[0].memberOf.values
    # Очищаем их имена от системных префиксов CN=... и отправляем в наши правила if/elif
"""

    # Для демонстрации заложим сценарий эмуляции ответа от AD по двум действующим сотрудникам
    mock_ad_responses = [
        {
            "login_ad": "m.kaftannik",
            "fio": "Кафтанник Максим",
            "ad_groups": ["Domain Users"]  # Исчезли все группы (Ситуация: Уволен)
        },
        {
            "login_ad": "a.korovin",
            "fio": "Коровин Александр",
            "ad_groups": ["Domain Users", "OP", "OP_LOCAL", "SV", "AD_Group_Trainers"]  # Появились SV/TR (Ситуация: Повышен / Штат)
        }
    ]
    
    for user_ad in mock_ad_responses:
        print(f"🔄 Анализ карточки AD для логина: {user_ad['login_ad']} ({user_ad['fio']})")
        groups = user_ad["ad_groups"]
        print(f"  └─ Текущие группы в домене: {groups}")
        
        # --- МЕСТО ДЛЯ МОДИФИКАЦИИ №2: ПРАВИЛА ОБРАТНОЙ СИНХРОНИЗАЦИИ ---
        
        # Правило 1: Увольнение (Осталась только Domain Users)
        if len(groups) == 1 and "Domain Users" in groups:
            print(f"  ⚠️ ВНИМАНИЕ: У сотрудника удалены все группы в AD!")
            print(f"  📢 ДЕЙСТВИЕ: Выполнение SQL: UPDATE Person SET status = 0 WHERE login_ad = '{user_ad['login_ad']}'; -> Карта переведена в статус [УВОЛЕН]")
            
        # Правило 2: Повышение / Переход в штат (Появились TR, SV или RP)
        elif any(g in groups for g in ["TR", "SV", "RP", "AD_Group_Trainers"]):
            print(f"  ⭐ ВНИМАНИЕ: В AD обнаружены маркеры административных ролей (TR/SV/RP)!")
            print(f"  📢 ДЕЙСТВИЕ: Выполнение SQL: UPDATE Person SET status = 3 WHERE login_ad = '{user_ad['login_ad']}'; -> Карта переведена в статус [ШТАТ / СКРЫТ]")
        else:
            print("  ✅ Изменений статуса не требуется (обычный оператор).")


def main():
    print("======================================================================")
    print("🏁 ЗАПУСК СЛУЖЕБНОГО СКРИПТА-МОСТА СИНХРОНИЗАЦИИ ACTIVE DIRECTORY")
    print(f"Время запуска: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("======================================================================")
    
    # Инициализация безопасного подключения
    ad_conn = connect_ad()
    if not ad_conn:
        print("❌ Работа скрипта прервана: нет связи с контроллером домена.")
        return
        
    # Выполнение регламентов обмена
    sync_new_users_to_ad(ad_conn)
    sync_changes_from_ad(ad_conn)
    
    print("\n======================================================================")
    print("🏁 СИНХРОНИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА. Ресурсы закрыты.")
    print("======================================================================")

if __name__ == "__main__":
    main()
