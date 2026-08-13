import os
import sqlite3

def init_database():
    # Подключаемся к файлу БД (если его нет, он создастся автоматически)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, 'UZ_OP.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Включаем поддержку внешних ключей (чтобы работало каскадное удаление)
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Таблица сотрудников (Person)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Person (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            second_name TEXT NOT NULL,
            curname TEXT,
            phone TEXT,
            birthday TEXT,
            login_ad TEXT UNIQUE,
            password_ad TEXT,
            email_telescope TEXT UNIQUE,
            email_cross TEXT UNIQUE,
            password_email TEXT
        );
    ''')

    # 2. Таблица локаций (Place)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Place (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    ''')

    # 3. Таблица проектов (Project)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Project (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    ''')

    # 4. Связующая таблица: Локации сотрудников (Place_Person)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Place_Person (
            id_pl INTEGER,
            id_per INTEGER,
            PRIMARY KEY (id_pl, id_per),
            FOREIGN KEY (id_pl) REFERENCES Place(id) ON DELETE CASCADE,
            FOREIGN KEY (id_per) REFERENCES Person(id) ON DELETE CASCADE
        );
    ''')

    # 5. Связующая таблица: Проекты сотрудников (Project_Person)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Project_Person (
            id_pr INTEGER,
            id_per INTEGER,
            PRIMARY KEY (id_pr, id_per),
            FOREIGN KEY (id_pr) REFERENCES Project(id) ON DELETE CASCADE,
            FOREIGN KEY (id_per) REFERENCES Person(id) ON DELETE CASCADE
        );
    ''')

    # 6. Таблица аккаунтов звонилки (TouchPoint)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS TouchPoint (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_pr INTEGER,
            id_per INTEGER,
            name TEXT NOT NULL,
            FOREIGN KEY (id_pr) REFERENCES Project(id) ON DELETE CASCADE,
            FOREIGN KEY (id_per) REFERENCES Person(id) ON DELETE CASCADE,
            UNIQUE(id_pr, id_per)
        );
    ''')
    conn.commit()
    print("ALL Correct!")
    conn.close()
if __name__ == '__main__':
    init_database()