import os
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor
import datetime

# ОДНОРАЗОВЫЙ первыичный ПАРСЕР УЗ для БД

def format_birthday(date_str):
    """Преобразует дату из Excel в формат YYYY-MM-DD для MariaDB"""
    if not date_str or str(date_str).lower() == 'nan': return None
    # Если пришел объект datetime от pandas
    if isinstance(date_str, (datetime.date, datetime.datetime)):
        return date_str.strftime('%Y-%m-%d')
    
    date_str = str(date_str).strip().replace('/', '.').replace('-', '.')
    parts = date_str.split('.')
    if len(parts) == 3:
        if len(parts[0]) == 4: # Формат ГГГГ.ММ.ДД
            return f"{parts[0]}-{parts[1]}-{parts[2]}"
        else: # Формат ДД.ММ.ГГГГ
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return None

def import_clean_data():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(current_dir, 'for_db.xlsx') 

    if not os.path.exists(excel_path):
        print(f"Error: File {os.path.basename(excel_path)} not found!")
        return

    print("Reading clean Excel file...")
    df = pd.read_excel(excel_path, converters={
        'Телефон': str, 'Пароль AD': str, 'Пароль E-mail': str, 'Дата рождения': str
    })
    df = df.dropna(subset=['Фамилия'])

    # Подключение к MariaDB
    conn = pymysql.connect(
        host=os.getenv('DB_HOST', '192.168.10.220'),
        port=int(os.getenv('DB_PORT', '3308')),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', '2nsfa@423FdsC0fHvL'),
        database=os.getenv('DB_NAME', 'MariaDB_to_test_Anton'),
        charset='utf8mb4',
        cursorclass=DictCursor,
        autocommit=False
    )

    try:
        with conn.cursor() as cursor:
            # --- СНАЧАЛА ОБНОВЛЯЕМ СПРАВОЧНИКИ ---
            print("Updating Locations...")
            if 'Локация' in df.columns:
                for place in df['Локация'].dropna().unique():
                    if str(place).strip():
                        cursor.execute("INSERT IGNORE INTO Location (name) VALUES (%s);", (str(place).strip(),))
            
            # --- ТЕПЕРЬ ЗАЛИВАЕМ ЛЮДЕЙ И ВСЕ СВЯЗИ ---
            print("Importing employees and complex relations...")
            for index, row in df.iterrows():
                if pd.isna(row.get('Фамилия')) or pd.isna(row.get('Имя')):
                    continue

                # Используем правильный маппинг: second_name=Фамилия, first_name=Имя, curname=Отчество
                s_name = str(row.get('Фамилия')).strip()
                f_name = str(row.get('Имя')).strip()
                c_name = str(row.get('Отчество', '')).strip() if pd.notna(row.get('Отчество')) else None
                
                login_ad = str(row.get('Логин AD', '')).strip() if pd.notna(row.get('Логин AD')) else None
                
                # Статус: "Работает" -> 1 (Активен), иначе -> 2 (Уволен)
                excel_status = str(row.get('Статус', '')).strip()
                id_status = 1 if excel_status.lower() in ['работает', 'активен'] else 2

                birthday = format_birthday(row.get('Дата рождения'))

                # 1. Заливаем сотрудника в Employee
                cursor.execute('''
                    INSERT IGNORE INTO Employee (
                        second_name, first_name, curname, phone, birthday, 
                        login_ad, password_ad, email_telescope, 
                        email_cross, password_email, id_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                ''', (
                    s_name, f_name, c_name,
                    str(row.get('Телефон', '')).strip() if pd.notna(row.get('Телефон')) else None,
                    birthday, login_ad,
                    str(row.get('Пароль AD', '')).strip() if pd.notna(row.get('Пароль AD')) else None,
                    str(row.get('E-mail Telescope', '')).strip() if pd.notna(row.get('E-mail Telescope')) else None,
                    str(row.get('E-mail Cross', '')).strip() if pd.notna(row.get('E-mail Cross')) else None,
                    str(row.get('Пароль E-mail', '')).strip() if pd.notna(row.get('Пароль E-mail')) else None,
                    id_status
                ))
                
                # Получаем ID сотрудника
                if login_ad:
                    cursor.execute("SELECT id FROM Employee WHERE login_ad = %s;", (login_ad,))
                else:
                    cursor.execute("SELECT id FROM Employee WHERE second_name = %s AND first_name = %s;", (s_name, f_name))
                    
                person_res = cursor.fetchone()
                if not person_res:
                    continue
                person_id = person_res['id']

                # 2. Связываем с Локацией (Location_Employee)
                if pd.notna(row.get('Локация')) and str(row.get('Локация')).strip():
                    cursor.execute("SELECT id FROM Location WHERE name = %s;", (str(row.get('Локация')).strip(),))
                    place_res = cursor.fetchone()
                    if place_res:
                        cursor.execute("INSERT IGNORE INTO Location_Employee (id_loc, id_emp) VALUES (%s, %s);", (place_res['id'], person_id))

                # 3. Связываем с Проектом (Project_Employee)
                project_id = None
                if pd.notna(row.get('Проект')) and str(row.get('Проект')).strip():
                    cursor.execute("SELECT id FROM Project WHERE name = %s;", (str(row.get('Проект')).strip(),))
                    project_res = cursor.fetchone()
                    
                    if project_res:
                        project_id = project_res['id']
                        # Добавляем должность OP (id_job = 7)
                        cursor.execute("INSERT IGNORE INTO Project_Employee (id_project, id_emp, id_job) VALUES (%s, %s, 7);", (project_id, person_id))
                    else:
                        print(f"Skipping unknown project: '{row.get('Проект')}' for {s_name}")

                # 4. Связываем Логин TouchPoint
                tp_login = str(row.get('Логин TouchPoint', '')).strip() if pd.notna(row.get('Логин TouchPoint')) else None
                if tp_login and project_id:
                    cursor.execute('''
                        INSERT IGNORE INTO TouchPoint (id_project, id_emp, name) 
                        VALUES (%s, %s, %s);
                    ''', (project_id, person_id, tp_login))

            conn.commit()
            print("\n[SUCCESS] All data imported successfully to MariaDB!")
            
    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Database error: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    import_clean_data()