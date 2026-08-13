import os
import sqlite3
import pandas as pd

def import_from_excel():
    # 1. Настраиваем пути к базе данных и Excel
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, 'UZ_OP.db')
    excel_path = os.path.join(current_dir, 'data.xlsx') # Имя вашего файла

    # Проверяем, лежит ли файл Excel на месте
    if not os.path.exists(excel_path):
        print(f"Error: File data.xlsx not found in {current_dir}!")
        return

    # 2. Читаем Excel в память Python
    print("Reading Excel file...")
    df = pd.read_excel(excel_path)

    # Подключаемся к нашей созданной базе данных
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 3. Заполняем справочник локаций (Place)
    # Вытаскиваем уникальные локации из колонки 'Локация' (замените на ваше имя колонки, если оно другое)
    if 'Локация' in df.columns:
        unique_places = df['Локация'].dropna().unique()
        for place in unique_places:
            # INSERT OR IGNORE не даст создать дубликат, если локация уже есть
            cursor.execute("INSERT OR IGNORE INTO Place (name) VALUES (?);", (str(place).strip(),))
        print(f"Imported {len(unique_places)} unique places.")

    # 4. Заполняем справочник проектов (Project)
    # Вытаскиваем уникальные проекты из колонки 'Проект'
    if 'Проект' in df.columns:
        unique_projects = df['Проект'].dropna().unique()
        for project in unique_projects:
            cursor.execute("INSERT OR IGNORE INTO Project (name) VALUES (?);", (str(project).strip(),))
        print(f"Imported {len(unique_projects)} unique projects.")

    # Сохраняем справочники
    conn.commit()
    
    print("Step 1 complete: Reference tables filled successfully!")
    conn.close()

if __name__ == '__main__':
    import_from_excel()