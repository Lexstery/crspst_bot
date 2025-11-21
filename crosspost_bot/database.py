import sqlite3

def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            is_admin BOOLEAN DEFAULT FALSE
        )
    ''')
    
    # Таблица каналов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channel_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            telegram_channel_id TEXT,
            vk_group_id TEXT,
            vk_token TEXT
        )
    ''')
    
    # Добавляем тестового пользователя (себя)
    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)",
        (1258360028, "@sentsuro", True)  # ЗАМЕНИ на свой Telegram ID!
    )
    
    # Добавляем тестовый канал
    cursor.execute(
        "INSERT OR IGNORE INTO channel_pairs (name, telegram_channel_id, vk_group_id, vk_token) VALUES (?, ?, ?, ?)",
        ("testcrspst", "@testcrspst", VK_GROUP_ID, VK_TOKEN)  # ЗАМЕНИ на свой канал!
    )
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована!")