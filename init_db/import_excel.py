import os
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor

def import_from_excel():
    # 1. Настраиваем пути к файлу Excel
    current_dir = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(current_dir, 'data.xlsx') # Имя вашего файла

    # Проверяем, лежит ли файл Excel на месте
    if not os.path.exists(excel_path):
        print(f"Error: File data.xlsx not found in {current_dir}!")
        return

    # 2. Читаем Excel в память Python
    print("Reading Excel file...")
    df = pd.read_excel(excel_path)

    # 3. Подключаемся к MariaDB (используем ваши актуальные данные)
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
            # 4. Заполняем справочник локаций (Location)
            if 'Локация' in df.columns:
                # dropna() убирает пустые ячейки, unique() оставляет только уникальные
                unique_places = df['Локация'].dropna().unique()
                for place in unique_places:
                    place_name = str(place).strip()
                    if not place_name or place_name.lower() == 'nan':
                        continue
                    # В MariaDB используется INSERT IGNORE, а вместо ? используется %s
                    cursor.execute("INSERT IGNORE INTO Location (name) VALUES (%s);", (place_name,))
                print(f"Imported {len(unique_places)} unique locations.")

            # 5. Заполняем справочник проектов (Project)
            if 'Проект' in df.columns:
                unique_projects = df['Проект'].dropna().unique()
                for project in unique_projects:
                    proj_name = str(project).strip()
                    if not proj_name or proj_name.lower() == 'nan':
                        continue
                    cursor.execute("INSERT IGNORE INTO Project (name) VALUES (%s);", (proj_name,))
                print(f"Imported {len(unique_projects)} unique projects.")

            # Сохраняем изменения
            conn.commit()
            print("Step 1 complete: Reference tables filled successfully!")
            
    except Exception as e:
        conn.rollback()
        print(f"Database error: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    import_from_excel()