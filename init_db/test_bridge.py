import sqlite3
import os
from ldap3 import Server, Connection, ALL, NTLM, MODIFY_ADD

# =====================================================================
# --- КОНФИГУРАЦИЯ (ЗАПОЛНИТЕ СВОИМИ ДАННЫМИ) ---
# =====================================================================
AD_SERVER = "ldap://192.168.192.2"  # IP или доменное имя вашего контроллера домена (можно ldap://)
AD_USER = "TELESCOPE-CC\\a.danilov"  # Или DOMAIN\username
AD_PASSWORD = "q2gyqJ4h1793"
AD_BASE_DN = "DC=TELESCOPE-CC,DC=ru"

TARGET_GROUP_AD = "OP_VOXYS_VTB"
TARGET_PROJECT_DB = "VOXYS_VTB" 

# Путь к вашей базе данных SQLite
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'UZ_OP.db')

# ВАЖНО: Укажите точное название таблицы и колонки в вашей БД, где хранятся логины
DB_TABLE = "Person" 
DB_LOGIN_COLUMN = "login_ad"
# =====================================================================

def connect_to_ad():
    """Ищет всех пользователей в указанной группе AD и возвращает список их логинов"""
    print(f"🔍 Подключение к AD и поиск группы")
    
    try:
        server = Server(AD_SERVER, get_info=ALL)
        conn = Connection(server, user=AD_USER, password=AD_PASSWORD, authentication=NTLM, auto_bind=True)
        return conn
    except Exception as e:
        print(f"❌ Ошибка подключения к AD: {e}")
        return None

def get_ad_users_and_group_dn(conn, group_name):
    """Возвращает DN группы и словарь {логин: DN} для всех её участников"""
    search_filter_group = f"(&(objectClass=group)(sAMAccountName={group_name}))"
    conn.search(AD_BASE_DN, search_filter_group, attributes=['distinguishedName'])
    
    if not conn.entries:
        print(f"❌ Группа '{group_name}' не найдена в AD!")
        return None, None
        
    group_dn = conn.entries[0].distinguishedName.value
    
    search_filter_users = f"(&(objectClass=user)(memberOf={group_dn}))"
    conn.search(AD_BASE_DN, search_filter_users, attributes=['sAMAccountName', 'distinguishedName'])
    
    ad_users = {}
    for entry in conn.entries:
        if entry.sAMAccountName:
            ad_users[entry.sAMAccountName.value.lower()] = entry.distinguishedName.value
            
    return group_dn, ad_users

def get_db_users(project_name):
    """Достает логины из БД для указанного проекта"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        query = """
            SELECT p.login_ad 
            FROM Person p
            JOIN Project_Person pp ON p.id = pp.id_per
            JOIN Project pr ON pp.id_pr = pr.id
            WHERE pr.name = ? AND p.login_ad IS NOT NULL
        """
        cursor.execute(query, (project_name,))
        db_users = {row[0].lower() for row in cursor.fetchall() if row[0]}
        conn.close()
        return db_users
    except Exception as e:
        print(f"❌ Ошибка чтения БД: {e}")
        return None

def print_current_state(ad_users, db_users):
    """Выводит текущий статус расхождений"""
    only_in_db = db_users - set(ad_users.keys())
    only_in_ad = set(ad_users.keys()) - db_users
    in_both = db_users & set(ad_users.keys())

    print(f"  🟢 Совпадают: {len(in_both)}")
    print(f"  🔴 Есть в БД, нет в AD (нужно добавить в AD): {len(only_in_db)}")
    print(f"  🟡 Есть в AD, нет в БД (нужно добавить в БД): {len(only_in_ad)}")
    return only_in_db, only_in_ad

def step_2_add_to_db(only_in_ad):
    """ШАГ 2: Добавляем проект в БД тем, у кого он есть в AD"""
    if not only_in_ad:
        print("  └─ Нет пользователей для добавления в БД.")
        return

    print(f"  └─ Добавляем проект '{TARGET_PROJECT_DB}' в БД для {len(only_in_ad)} чел...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Получаем ID проекта
        cursor.execute("SELECT id FROM Project WHERE name = ?", (TARGET_PROJECT_DB,))
        project_row = cursor.fetchone()
        if not project_row:
            print(f"  ❌ ОШИБКА: Проект '{TARGET_PROJECT_DB}' не найден в таблице Project!")
            return
        id_pr = project_row[0]

        added_count = 0
        for login in only_in_ad:
            # Ищем ID пользователя по логину
            cursor.execute("SELECT id FROM Person WHERE login_ad = ?", (login,))
            person_row = cursor.fetchone()
            if person_row:
                id_per = person_row[0]
                # Добавляем связь (INSERT OR IGNORE защитит от ошибки, если связь уже есть)
                cursor.execute("INSERT OR IGNORE INTO Project_Person (id_pr, id_per) VALUES (?, ?)", (id_pr, id_per))
                added_count += 1
            else:
                print(f"    ⚠️ Логин {login} есть в AD, но отсутствует в таблице Person! Пропущен.")
        
        conn.commit()
        conn.close()
        print(f"  ✅ Успешно добавлено связей в БД: {added_count}")
    except Exception as e:
        print(f"  ❌ Ошибка записи в БД: {e}")

def step_4_add_to_ad(ad_conn, group_dn, only_in_db):
    """ШАГ 4: Добавляем группу в AD тем, у кого она есть в БД"""
    if not only_in_db:
        print("  └─ Нет пользователей для добавления в AD.")
        return

    print(f"  └─ Добавляем группу '{TARGET_GROUP_AD}' в AD для {len(only_in_db)} чел...")
    added_count = 0
    
    for login in only_in_db:
        # Нам нужен DN пользователя. Ищем его в AD (он может быть в системе, но не в этой группе)
        search_filter = f"(&(objectClass=user)(sAMAccountName={login}))"
        ad_conn.search(AD_BASE_DN, search_filter, attributes=['distinguishedName'])
        
        if ad_conn.entries:
            user_dn = ad_conn.entries[0].distinguishedName.value
            # Добавляем пользователя в группу
            ad_conn.modify(group_dn, {'member': [(MODIFY_ADD, [user_dn])]})
            
            if ad_conn.result['description'] == 'success':
                added_count += 1
            else:
                print(f"    ⚠️ Не удалось добавить {login} в AD. Причина: {ad_conn.result['description']}")
        else:
            print(f"    ⚠️ Логин {login} есть в БД, но вообще не найден в AD! Пропущен.")

    print(f"  ✅ Успешно добавлено в AD: {added_count}")


if __name__ == "__main__":
    print("🏁 ЗАПУСК ПРОЦЕССА СИНХРОНИЗАЦИИ AD И БД\n")
    
    ad_conn = connect_to_ad()
    if not ad_conn: exit()

    # --- ШАГ 1: Вывод как есть сейчас ---
    print("--- ШАГ 1: ТЕКУЩИЙ СТАТУС ---")
    group_dn, ad_users = get_ad_users_and_group_dn(ad_conn, TARGET_GROUP_AD)
    db_users = get_db_users(TARGET_PROJECT_DB)
    
    if ad_users is None or db_users is None: exit()
    only_in_db, only_in_ad = print_current_state(ad_users, db_users)

    # --- ШАГ 2: Добавляем в БД тех, кто есть в АД ---
    print("\n--- ШАГ 2: СИНХРОНИЗАЦИЯ AD ➔ БД ---")
    step_2_add_to_db(only_in_ad)

    # --- ШАГ 3: Вывод обновленного списка ---
    print("\n--- ШАГ 3: ПРОВЕРКА ПОСЛЕ ОБНОВЛЕНИЯ БД ---")
    db_users_updated = get_db_users(TARGET_PROJECT_DB)
    print_current_state(ad_users, db_users_updated)

    # --- ШАГ 4: Добавляем в АД тех, кто есть в БД ---
    print("\n--- ШАГ 4: СИНХРОНИЗАЦИЯ БД ➔ AD ---")
    step_4_add_to_ad(ad_conn, group_dn, only_in_db)

    # --- ШАГ 5: Финальный вывод ---
    print("\n--- ШАГ 5: ФИНАЛЬНЫЙ СТАТУС (ДОЛЖНЫ СОВПАДАТЬ) ---")
    # Перезапрашиваем данные из АД, так как мы только что туда кое-что добавили
    _, ad_users_final = get_ad_users_and_group_dn(ad_conn, TARGET_GROUP_AD)
    db_users_final = get_db_users(TARGET_PROJECT_DB)
    
    if ad_users_final is not None and db_users_final is not None:
        print_current_state(ad_users_final, db_users_final)
    
    ad_conn.unbind()
    print("\n🏁 РАБОТА ЗАВЕРШЕНА.")