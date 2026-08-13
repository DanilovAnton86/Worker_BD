import os
import sqlite3
import pandas as pd

def import_clean_data():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, 'UZ_OP.db')
    
    # Укажите имя вашего очищенного Excel-файла
    excel_path = os.path.join(current_dir, 'for_db.xlsx') 

    if not os.path.exists(excel_path):
        print(f"Error: File {os.path.basename(excel_path)} not found!")
        return

    print("Reading clean Excel file...")
    # Читаем все колонки как текст, чтобы номера и пароли не поплыли
    df = pd.read_excel(excel_path, converters={
        'Телефон': str, 
        'Пароль AD': str, 
        'Пароль E-mail': str,
        'Дата рождения': str  # <- Эта строчка заберет дату из Excel прямо в текстовом виде, и она не поплывет!
    })
    
    # Жестко вычищаем из памяти пустые строки внизу файла
    df = df.dropna(subset=['Фамилия'])

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # --- СНАЧАЛА ОБНОВЛЯЕМ СПРАВОЧНИКИ (Дальние ветки) ---
    print("Updating Places and Projects...")
    if 'Локация' in df.columns:
        for place in df['Локация'].dropna().unique():
            if str(place).strip():
                cursor.execute("INSERT OR IGNORE INTO Place (name) VALUES (?);", (str(place).strip(),))
            
    #if 'Проект' in df.columns:
    #   for project in df['Проект'].dropna().unique():
    #       if str(project).strip():
    #           cursor.execute("INSERT OR IGNORE INTO Project (name) VALUES (?);", (str(project).strip(),))
    #conn.commit()
    #cursor.execute("BEGIN TRANSACTION;")
    # --- ТЕПЕРЬ ЗАЛИВАЕМ ЛЮДЕЙ И ВСЕ СВЯЗИ ---
    print("Importing employees and complex relations...")
    for index, row in df.iterrows():
        # Пропускаем строки без ФИО
        if pd.isna(row.get('Фамилия')) or pd.isna(row.get('Имя')):
            continue

        login_ad = str(row.get('Логин AD', '')).strip() if pd.notna(row.get('Логин AD')) else None
        
        # Определяем числовой статус: если в Excel написано "Работает", ставим 1, иначе 0
        excel_status = str(row.get('Статус', '')).strip()
        status_value = 1 if excel_status == "Работает" else 0

        # 1. Заливаем сотрудника в Person со всеми вашими полями
        cursor.execute('''
            INSERT OR IGNORE INTO Person (
                first_name, second_name, curname, phone, birthday, 
                login_ad, password_ad, email_telescope, 
                email_cross, password_email, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        ''', (
            str(row.get('Фамилия')).strip(),
            str(row.get('Имя')).strip(),
            str(row.get('Отчество', '')).strip() if pd.notna(row.get('Отчество')) else None,
            str(row.get('Телефон', '')).strip() if pd.notna(row.get('Телефон')) else None,
            str(row.get('Дата рождения', '')).strip() if pd.notna(row.get('Дата рождения')) else None,
            login_ad,
            str(row.get('Пароль AD', '')).strip() if pd.notna(row.get('Пароль AD')) else None,
            str(row.get('E-mail Telescope', '')).strip() if pd.notna(row.get('E-mail Telescope')) else None,
            str(row.get('E-mail Cross', '')).strip() if pd.notna(row.get('E-mail Cross')) else None,
            str(row.get('Пароль E-mail', '')).strip() if pd.notna(row.get('Пароль E-mail')) else None,
            status_value
        ))
        
        # Для создания связей нам позарез нужен ID этого сотрудника из базы
        if login_ad:
            cursor.execute("SELECT id FROM Person WHERE login_ad = ?;", (login_ad,))
            person_res = cursor.fetchone()
            if not person_res:
                continue
            person_id = person_res[0]

            # 2. Связываем с Локацией (Place_Person)
            if pd.notna(row.get('Локация')) and str(row.get('Локация')).strip():
                cursor.execute("SELECT id FROM Place WHERE name = ?;", (str(row.get('Локация')).strip(),))
                place_res = cursor.fetchone()
                if place_res:
                    cursor.execute("INSERT OR IGNORE INTO Place_Person (id_pl, id_per) VALUES (?, ?);", (place_res[0], person_id))

            # 3. Связываем с Проектом (Project_Person)
            project_id = None
            if pd.notna(row.get('Проект')) and str(row.get('Проект')).strip():
                # Ищем проект в нашей эталонной базе
                cursor.execute("SELECT id FROM Place WHERE name = ?;", (str(row.get('Проект')).strip(),)) # Ой, тут была опечатка в старых версиях (выбирали из Place)! Вот правильная строка:
                cursor.execute("SELECT id FROM Project WHERE name = ?;", (str(row.get('Проект')).strip(),))
                project_res = cursor.fetchone()
                
                # ЕСЛИ НАШЛИ — берем его ID для связи
                if project_res:
                    project_id = project_res[0]
                    cursor.execute("INSERT OR IGNORE INTO Project_Person (id_pr, id_per) VALUES (?, ?);", (project_id, person_id))
                else:
                    # ЕСЛИ НЕ НАШЛИ (мусорное слово) — проект просто игнорируется, ячейка связи остается пустой (NULL)
                    print(f"Skipping unknown project word: '{row.get('Проект')}' for {row.get('Фамилия')}")


            # 4. Связываем Логин TouchPoint (Берем проект, человека и пишем логин звонилки)
            # Запись создастся, только если у человека есть и проект, и сам логин TouchPoint в Excel
            tp_login = str(row.get('Логин TouchPoint', '')).strip() if pd.notna(row.get('Логин TouchPoint')) else None
            if tp_login and project_id:
                cursor.execute('''
                    INSERT OR IGNORE INTO TouchPoint (id_pr, id_per, name) 
                    VALUES (?, ?, ?);
                ''', (project_id, person_id, tp_login))

    conn.commit()
    conn.close()
    print("\n[SUCCESS] All data, statuses, emails, and TouchPoint accounts imported successfully!")

if __name__ == '__main__':
    import_clean_data()