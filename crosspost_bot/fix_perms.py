import sqlite3

def fix_all_permissions():
    """Исправить права доступа для всех пользователей и каналов"""
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    
    # Получаем всех одобренных пользователей
    cursor.execute("SELECT id FROM users WHERE is_approved = TRUE")
    users = cursor.fetchall()
    
    # Получаем все активные каналы
    cursor.execute("SELECT id FROM channels WHERE is_active = TRUE")
    channels = cursor.fetchall()
    
    # Даем каждому пользователю доступ ко всем каналам
    permissions_count = 0
    for user in users:
        for channel in channels:
            cursor.execute(
                "INSERT OR REPLACE INTO user_permissions (user_id, channel_id, can_post) VALUES (?, ?, ?)",
                (user[0], channel[0], True)
            )
            permissions_count += 1
    
    conn.commit()
    conn.close()
    
    print(f"✅ Выдано {permissions_count} прав доступа:")
    print(f"   • Пользователей: {len(users)}")
    print(f"   • Каналов: {len(channels)}")
    print(f"   • Каждый пользователь имеет доступ ко всем каналам")

if __name__ == "__main__":
    fix_all_permissions()