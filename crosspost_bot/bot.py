import logging
import sqlite3
import re
from urllib.parse import unquote
from telegram import Update, InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from vk_api import VkApi
from vk_api.upload import VkUpload
from io import BytesIO

from config import TELEGRAM_TOKEN, VK_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AdminControlledReplyBot:
    def __init__(self):
        self.tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.setup_handlers()
        self.init_database()
        
        # Инициализируем VK с обработкой ошибок
        self.vk_api = None
        self.vk_upload = None
        self.init_vk_api()
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.tg_app.add_handler(CommandHandler("start", self.start_command))
        self.tg_app.add_handler(CommandHandler("menu", self.show_main_menu))
        self.tg_app.add_handler(CommandHandler("hide", self.hide_keyboard))
        self.tg_app.add_handler(CommandHandler("status", self.status_command))
        self.tg_app.add_handler(CommandHandler("get_token", self.get_token_command))
        self.tg_app.add_handler(CommandHandler("update_token", self.update_token_command))
        self.tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message_with_token))
        self.tg_app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
    
    def init_vk_api(self):
        """Инициализация VK API с обработкой ошибок"""
        try:
            self.vk_session = VkApi(token=VK_TOKEN)
            self.vk_api = self.vk_session.get_api()
            self.vk_upload = VkUpload(self.vk_session)
            logger.info("✅ VK API инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка VK API: {e}")
            self.vk_api = None
            self.vk_upload = None
    
    def check_vk_token(self):
        """Проверка валидности VK токена"""
        if not self.vk_api:
            return False
        
        try:
            # Пробуем безопасный метод для проверки токена
            self.vk_api.users.get()
            return True
        except Exception as e:
            logger.error(f"VK токен невалиден: {e}")
            return False
    
    def get_vk_token_message(self):
        """Сообщение с инструкцией по получению нового токена"""
        token_message = (
            "🔑 **VK токен истек или невалиден!**\n\n"
            "Чтобы получить новый токен:\n\n"
            "1. **Перейди по ссылке:**\n"
            "https://oauth.vk.com/authorize?client_id=6121396&scope=photos,groups,wall,offline&redirect_uri=https://oauth.vk.com/blank.html&display=page&v=5.199&response_type=token\n\n"
            "2. **Скопируй токен из адресной строки** (часть между `access_token=` и `&expires_in`)\n\n"
            "3. **Обнови токен командой:**\n"
            "`/update_token твой_новый_токен`\n\n"
            "**Или просто отправь ссылку из адресной строки** - бот автоматически извлечет токен!\n\n"
            "📎 **Ссылка для копирования:**\n"
            "`https://oauth.vk.com/authorize?client_id=6121396&scope=photos,groups,wall,offline&redirect_uri=https://oauth.vk.com/blank.html&display=page&v=5.199&response_type=token`"
        )
        return token_message
    
    def extract_token_from_input(self, input_text: str) -> str:
        """Извлекает токен из различных форматов ввода"""
        # Если это URL с токеном
        if 'access_token=' in input_text:
            # Декодируем URL
            decoded_url = unquote(input_text)
            
            # Ищем токен в URL - ВСЕ символы до следующего параметра (&)
            token_match = re.search(r'access_token=([^&]+)', decoded_url)
            if token_match:
                return token_match.group(1)
        
        # Если это прямая ссылка на oauth.vk.com
        elif 'oauth.vk.com' in input_text:
            # Пробуем извлечь из фрагмента URL
            fragment_match = re.search(r'#(.+)', input_text)
            if fragment_match:
                fragment = fragment_match.group(1)
                token_match = re.search(r'access_token=([^&]+)', fragment)
                if token_match:
                    return token_match.group(1)
        
        # Если это просто токен (может содержать буквы, цифры, точки, дефисы, подчеркивания)
        elif re.match(r'^[a-zA-Z0-9\.\-_]+$', input_text.strip()):
            return input_text.strip()
        
        return None
    
    def update_vk_token(self, new_token: str) -> bool:
        """Обновляет VK токен в памяти и в файле .env"""
        try:
            # Обновляем в памяти
            global VK_TOKEN
            VK_TOKEN = new_token
            
            # Переинициализируем VK API
            self.init_vk_api()
            
            # Обновляем в файле .env
            self.update_env_file(new_token)
            
            logger.info("✅ VK токен успешно обновлен")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления токена: {e}")
            return False
    
    def update_env_file(self, new_token: str):
        """Обновляет токен в файле .env"""
        try:
            # Читаем текущий файл
            with open('.env', 'r', encoding='utf-8') as file:
                lines = file.readlines()
            
            # Обновляем или добавляем VK_TOKEN
            token_updated = False
            new_lines = []
            
            for line in lines:
                if line.startswith('VK_TOKEN='):
                    new_lines.append(f'VK_TOKEN={new_token}\n')
                    token_updated = True
                else:
                    new_lines.append(line)
            
            # Если токен не был найден, добавляем новую строку
            if not token_updated:
                new_lines.append(f'VK_TOKEN={new_token}\n')
            
            # Записываем обратно
            with open('.env', 'w', encoding='utf-8') as file:
                file.writelines(new_lines)
                
            logger.info("✅ Файл .env обновлен")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления .env файла: {e}")
            raise
    
    async def update_token_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для обновления VK токена через ссылку"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        # Проверяем, есть ли ссылка в сообщении
        if not context.args:
            await update.message.reply_text(
                "🔧 **Обновление VK токена**\n\n"
                "Отправьте команду в формате:\n"
                "`/update_token https://oauth.vk.com/blank.html#access_token=ваш_токен&expires_in=...`\n\n"
                "Или просто отправьте новый токен:\n"
                "`/update_token ваш_новый_токен`\n\n"
                "Также можно просто отправить ссылку в чат - бот автоматически распознает токен!"
            )
            return
        
        token_input = ' '.join(context.args)
        new_token = self.extract_token_from_input(token_input)
        
        if not new_token:
            await update.message.reply_text(
                "❌ Не удалось извлечь токен из ссылки.\n\n"
                "Проверьте формат:\n"
                "• Ссылка должна содержать `access_token=...`\n"
                "• Или отправьте только токен\n"
                f"Полученный ввод: {token_input[:100]}..."
            )
            return
        
        # Обновляем токен
        if self.update_vk_token(new_token):
            await update.message.reply_text(
                f"✅ VK токен успешно обновлен!\n\n"
                f"Токен: `{new_token[:15]}...{new_token[-10:]}`\n"
                f"Длина токена: {len(new_token)} символов\n\n"
                f"Статус VK: {'✅ Работает' if self.check_vk_token() else '❌ Ошибка'}\n\n"
                f"Проверьте статус: /status"
            )
        else:
            await update.message.reply_text("❌ Ошибка при обновлении токена")
    
    async def handle_token_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка сообщений с токенами (автоматическое определение)"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            return
        
        text = update.message.text
        
        # Проверяем, содержит ли сообщение токен VK
        if any(keyword in text for keyword in ['access_token=', 'oauth.vk.com']):
            new_token = self.extract_token_from_input(text)
            
            if new_token:
                if self.update_vk_token(new_token):
                    await update.message.reply_text(
                        f"✅ VK токен автоматически обновлен!\n\n"
                        f"Токен: `{new_token[:15]}...{new_token[-10:]}`\n"
                        f"Длина токена: {len(new_token)} символов\n\n"
                        f"Статус VK: {'✅ Работает' if self.check_vk_token() else '❌ Ошибка'}\n\n"
                        f"Проверьте статус: /status"
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при автоматическом обновлении токена")
    
    async def handle_text_message_with_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Объединенный обработчик текстовых сообщений"""
        # Сначала проверяем токен
        await self.handle_token_message(update, context)
        # Затем стандартная обработка
        await self.handle_text_message(update, context)
    
    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        # Таблица пользователей с approved статусом
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                is_approved BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица каналов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                telegram_channel TEXT NOT NULL,
                vk_group_id TEXT NOT NULL,
                created_by INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users (id)
            )
        ''')
        
        # Таблица прав доступа
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_permissions (
                user_id INTEGER,
                channel_id INTEGER,
                can_post BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (user_id, channel_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (channel_id) REFERENCES channels (id)
            )
        ''')
        
        # Добавляем первого пользователя как администратора
        cursor.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name, is_admin, is_approved) VALUES (?, ?, ?, ?, ?)",
            (1258360028, "@sentsuro", "Андрей", True, True)
        )
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def get_user(self, telegram_id):
        """Получаем информацию о пользователе"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, username, first_name, is_admin, is_approved FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        user = cursor.fetchone()
        
        conn.close()
        
        if user:
            return {
                'id': user[0],
                'username': user[1],
                'first_name': user[2],
                'is_admin': bool(user[3]),
                'is_approved': bool(user[4])
            }
        return None
    
    def register_user(self, telegram_id, username, first_name):
        """Регистрируем нового пользователя (не approved)"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name, is_approved) VALUES (?, ?, ?, ?)",
            (telegram_id, username, first_name, False)
        )
        
        conn.commit()
        conn.close()
    
    def is_user_approved(self, telegram_id):
        """Проверяем approved ли пользователь"""
        user = self.get_user(telegram_id)
        return user and user['is_approved']
    
    def get_pending_users(self):
        """Получаем список пользователей ожидающих approval"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT telegram_id, username, first_name FROM users WHERE is_approved = FALSE"
        )
        users = cursor.fetchall()
        conn.close()
        
        return [{
            'telegram_id': user[0],
            'username': user[1],
            'first_name': user[2]
        } for user in users]
    
    def approve_user(self, telegram_id):
        """Одобряем пользователя и даем доступ ко всем каналам"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE users SET is_approved = TRUE WHERE telegram_id = ?",
            (telegram_id,)
        )
        
        # Получаем ID пользователя
        cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        user_result = cursor.fetchone()
        
        if user_result:
            user_id = user_result[0]
            
            # Получаем все активные каналы
            cursor.execute("SELECT id FROM channels WHERE is_active = TRUE")
            channels = cursor.fetchall()
            
            # Даем доступ ко всем каналам
            for channel in channels:
                cursor.execute(
                    "INSERT OR REPLACE INTO user_permissions (user_id, channel_id, can_post) VALUES (?, ?, ?)",
                    (user_id, channel[0], True)
                )
            
            logger.info(f"✅ Пользователь {telegram_id} одобрен и получил доступ к {len(channels)} каналам")
        
        conn.commit()
        conn.close()
    
    def grant_access_to_all_users(self, channel_id):
        """Выдать доступ к новому каналу всем одобренным пользователям"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        # Получаем всех одобренных пользователей
        cursor.execute("SELECT id FROM users WHERE is_approved = TRUE")
        users = cursor.fetchall()
        
        # Даем каждому пользователю доступ к новому каналу
        for user in users:
            cursor.execute(
                "INSERT OR REPLACE INTO user_permissions (user_id, channel_id, can_post) VALUES (?, ?, ?)",
                (user[0], channel_id, True)
            )
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Все одобренные пользователи получили доступ к каналу {channel_id}")
    
    def get_user_channels(self, user_id):
        """Получаем каналы доступные пользователю"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT c.id, c.name, c.telegram_channel, c.vk_group_id 
            FROM channels c
            LEFT JOIN user_permissions up ON c.id = up.channel_id AND up.user_id = ?
            WHERE c.is_active = TRUE AND (up.can_post = TRUE OR c.created_by = ? OR 
                  (SELECT is_admin FROM users WHERE id = ?) = TRUE)
        ''', (user_id, user_id, user_id))
        
        channels = cursor.fetchall()
        conn.close()
        
        return [{
            'id': channel[0],
            'name': channel[1],
            'telegram': channel[2],
            'vk_group_id': channel[3]
        } for channel in channels]
    
    def get_all_channels(self):
        """Получаем все каналы (для админов)"""
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name, telegram_channel, vk_group_id FROM channels WHERE is_active = TRUE")
        channels = cursor.fetchall()
        
        conn.close()
        
        return [{
            'id': channel[0],
            'name': channel[1],
            'telegram': channel[2],
            'vk_group_id': channel[3]
        } for channel in channels]
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статус подключений"""
        vk_status = "✅ Работает" if self.check_vk_token() else "❌ Истек/Невалиден"
        tg_status = "✅ Работает"
        
        message = (
            f"📊 **Статус бота:**\n\n"
            f"Telegram API: {tg_status}\n"
            f"VK API: {vk_status}\n\n"
        )
        
        if not self.check_vk_token():
            message += self.get_vk_token_message()
        else:
            message += "Все системы работают нормально! 🚀"
        
        await update.message.reply_text(message)
    
    async def get_token_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для получения ссылки на новый токен"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        await update.message.reply_text(self.get_vk_token_message())
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация/приветствие пользователя"""
        user = update.effective_user
        
        # Регистрируем пользователя
        self.register_user(user.id, user.username, user.first_name)
        user_info = self.get_user(user.id)
        
        if not user_info['is_approved']:
            await update.message.reply_text(
                "⏳ Ваш аккаунт ожидает одобрения администратором.\n\n"
                "Как только администратор одобрит ваш доступ, вы сможете пользоваться ботом.\n\n"
                "Для проверки статуса используйте /start"
            )
            return
        
        # Показываем главное меню
        await self.show_main_menu(update, context)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показываем главное меню с Reply Keyboard"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_approved']:
            await update.message.reply_text("❌ Доступ запрещен. Ожидайте одобрения администратора.")
            return
        
        if user_info['is_admin']:
            # Меню для администратора
            keyboard = [
                ["📢 Опубликовать пост", "📋 Мои каналы"],
                ["👥 Управление пользователями", "⚙️ Управление каналами"],
                ["👑 Управление админами", "ℹ️ Помощь"],
                ["❌ Скрыть меню"]
            ]
        else:
            # Меню для обычного пользователя
            keyboard = [
                ["📢 Опубликовать пост", "📋 Мои каналы"],
                ["ℹ️ Помощь", "❌ Скрыть меню"]
            ]
        
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True,
            one_time_keyboard=False
        )
        
        role_text = "👑 Администратор" if user_info['is_admin'] else "👤 Пользователь"
        vk_status = "✅" if self.check_vk_token() else "❌"
        
        message = (
            f"🎯 **Главное меню**\n\n"
            f"Роль: {role_text}\n"
            f"Имя: {user_info['first_name']}\n"
            f"VK: {vk_status}\n\n"
            f"Выберите действие:"
        )
        
        if not self.check_vk_token() and user_info['is_admin']:
            message += f"\n\n⚠️ VK токен истек! Используйте /get_token для получения нового"
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def hide_keyboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Скрыть клавиатуру"""
        await update.message.reply_text(
            "Меню скрыто. Для показа используйте /menu",
            reply_markup=ReplyKeyboardRemove()
        )
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ВСЕХ текстовых сообщений"""
        user = update.effective_user
        text = update.message.text
        
        if not self.is_user_approved(user.id):
            await update.message.reply_text("❌ Доступ запрещен. Ожидайте одобрения администратора.")
            return
        
        user_info = self.get_user(user.id)
        
        # Обработка кнопок главного меню
        if text == "📢 Опубликовать пост":
            await self.show_publish_menu(update, context)
        
        elif text == "📋 Мои каналы":
            await self.show_my_channels(update, context)
        
        elif text == "👥 Управление пользователями" and user_info['is_admin']:
            await self.show_user_management(update, context)
        
        elif text == "⚙️ Управление каналами" and user_info['is_admin']:
            await self.show_channel_management(update, context)
        
        elif text == "👑 Управление админами" and user_info['is_admin']:
            await self.admin_management(update, context)
        
        elif text == "👥 Список админов" and user_info['is_admin']:
            await self.show_admins_list(update, context)
        
        elif text == "➕ Добавить админа" and user_info['is_admin']:
            await self.start_add_admin(update, context)
        
        elif text == "ℹ️ Помощь":
            await self.show_help(update, context)
        
        elif text == "❌ Скрыть меню":
            await self.hide_keyboard(update, context)
        
        elif text == "🔙 Назад в меню":
            await self.show_main_menu(update, context)
        
        elif text == "✅ Одобрить всех пользователей" and user_info['is_admin']:
            await self.approve_all_users(update, context)
        
        elif text == "➕ Добавить канал" and user_info['is_admin']:
            await self.start_add_channel(update, context)
        
        # Если это выбор канала для публикации
        elif text.startswith("📢 "):
            channel_name = text[2:]  # Убираем эмодзи
            await self.select_channel_for_publishing(update, context, channel_name)
        
        # Обработка процесса добавления канала
        elif 'setup_stage' in context.user_data:
            await self.handle_channel_setup(update, context, text)
        
        # Если это обычный текст и выбран канал - публикуем
        elif 'selected_channel' in context.user_data:
            await self.publish_text(update, context, text)
        
        else:
            await update.message.reply_text(
                "ℹ️ Используйте меню для навигации или /menu для показа меню"
            )
    
    async def show_publish_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Меню публикации"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        channels = self.get_user_channels(user_info['id'])
        
        if not channels:
            keyboard = [["🔙 Назад в меню"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "❌ У вас нет доступных каналов для публикации.\n\n"
                "Обратитесь к администратору для получения доступа.",
                reply_markup=reply_markup
            )
            return
        
        # Создаем кнопки для выбора канала
        keyboard = []
        for channel in channels:
            keyboard.append([f"📢 {channel['name']}"])
        
        keyboard.append(["🔙 Назад в меню"])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        vk_status = "✅ VK работает" if self.check_vk_token() else "❌ VK недоступен (токен истек)"
        
        message = (
            f"🎯 **Выберите канал для публикации:**\n"
            f"{vk_status}\n\n"
            f"Нажмите на кнопку с названием канала, затем отправьте текст или фото:"
        )
        
        if not self.check_vk_token() and user_info['is_admin']:
            message += f"\n\n⚠️ Для получения нового токена используйте /get_token"
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def select_channel_for_publishing(self, update: Update, context: ContextTypes.DEFAULT_TYPE, channel_name: str):
        """Выбор канала для публикации"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        channels = self.get_user_channels(user_info['id'])
        
        channel = next((ch for ch in channels if ch['name'] == channel_name), None)
        
        if channel:
            context.user_data['selected_channel'] = channel
            keyboard = [["🔙 Назад в меню"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            vk_status = "✅ Будет опубликовано в VK" if self.check_vk_token() else "⚠️ Только в Telegram (VK токен истек)"
            
            message = (
                f"✅ **Выбран канал:** {channel['name']}\n"
                f"{vk_status}\n\n"
                f"Теперь отправьте текст или фото для публикации.\n"
                f"Пост будет опубликован в:\n"
                f"• Telegram: {channel['telegram']}\n"
                f"• VK: {channel['vk_group_id']}"
            )
            
            if not self.check_vk_token() and user_info['is_admin']:
                message += f"\n\n🔧 Для обновления токена: /get_token или /update_token"
            
            await update.message.reply_text(message, reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ Канал не найден")
    
    async def show_my_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать каналы пользователя"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        channels = self.get_user_channels(user_info['id'])
        
        if not channels:
            message = "❌ У вас нет доступных каналов.\n\nОбратитесь к администратору для получения доступа."
        else:
            message = "📋 **Ваши каналы:**\n\n"
            for channel in channels:
                message += f"• {channel['name']}\n"
                message += f"  📱 TG: {channel['telegram']}\n"
                message += f"  👥 VK: {channel['vk_group_id']}\n\n"
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def show_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Управление пользователями (админ)"""
        pending_users = self.get_pending_users()
        
        if not pending_users:
            message = "✅ Нет пользователей ожидающих одобрения."
            keyboard = [["🔙 Назад в меню"]]
        else:
            message = "👥 **Пользователи ожидающие одобрения:**\n\n"
            for user in pending_users:
                message += f"• {user['first_name']} (@{user['username']})\n"
            
            message += "\nДля одобрения всех пользователей нажмите кнопку ниже:"
            keyboard = [
                ["✅ Одобрить всех пользователей"],
                ["🔙 Назад в меню"]
            ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def approve_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Одобрить всех ожидающих пользователей"""
        pending_users = self.get_pending_users()
        
        for user in pending_users:
            self.approve_user(user['telegram_id'])
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ Все пользователи ({len(pending_users)}) одобрены и получили доступ к каналам!",
            reply_markup=reply_markup
        )
    
    async def show_channel_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Управление каналами (админ)"""
        channels = self.get_all_channels()
        
        message = "⚙️ **Управление каналами**\n\n"
        
        if not channels:
            message += "❌ Нет созданных каналов.\n\n"
        else:
            message += "📋 **Список ваших каналов:**\n\n"
            for channel in channels:
                message += f"• {channel['name']}\n"
                message += f"  📱 TG: {channel['telegram']}\n"
                message += f"  👥 VK: {channel['vk_group_id']}\n\n"
        
        message += "Для добавления нового канала нажмите кнопку ниже:"
        
        keyboard = [
            ["➕ Добавить канал"],
            ["🔙 Назад в меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def start_add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало процесса добавления канала"""
        context.user_data['setup_stage'] = 'awaiting_name'
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📝 **Добавление нового канала**\n\n"
            "Шаг 1/3: Введите название канала (например: 'Новости компании'):",
            reply_markup=reply_markup
        )
    
    async def handle_channel_setup(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Обработка процесса добавления канала"""
        user_data = context.user_data
        stage = user_data['setup_stage']
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        if stage == 'awaiting_name':
            user_data['new_channel_name'] = text
            user_data['setup_stage'] = 'awaiting_telegram'
            await update.message.reply_text(
                "✅ Название сохранено!\n\n"
                "Шаг 2/3: Введите username Telegram канала (например: @my_channel):",
                reply_markup=reply_markup
            )
            
        elif stage == 'awaiting_telegram':
            user_data['new_telegram_channel'] = text
            user_data['setup_stage'] = 'awaiting_vk'
            await update.message.reply_text(
                "✅ Telegram канал сохранен!\n\n"
                "Шаг 3/3: Введите ID VK группы (например: -123456789):",
                reply_markup=reply_markup
            )
            
        elif stage == 'awaiting_vk':
            user_data['new_vk_group_id'] = text
            
            # Сохраняем канал в базу данных
            conn = sqlite3.connect('bot.db')
            cursor = conn.cursor()
            
            user = update.effective_user
            user_info = self.get_user(user.id)
            
            cursor.execute(
                "INSERT INTO channels (name, telegram_channel, vk_group_id, created_by) VALUES (?, ?, ?, ?)",
                (user_data['new_channel_name'], user_data['new_telegram_channel'], user_data['new_vk_group_id'], user_info['id'])
            )
            
            channel_id = cursor.lastrowid
            
            # Автоматически даем доступ создателю
            cursor.execute(
                "INSERT OR REPLACE INTO user_permissions (user_id, channel_id, can_post) VALUES (?, ?, ?)",
                (user_info['id'], channel_id, True)
            )
            
            conn.commit()
            conn.close()
            
            # Даем доступ к новому каналу всем одобренным пользователям
            self.grant_access_to_all_users(channel_id)
            
            # Очищаем временные данные
            context.user_data.clear()
            
            await update.message.reply_text(
                f"🎉 Канал '{user_data['new_channel_name']}' успешно добавлен!\n\n"
                "Все одобренные пользователи автоматически получили доступ к этому каналу.",
                reply_markup=reply_markup
            )
    
    async def show_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать помощь"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        message = "ℹ️ **Помощь по использованию бота**\n\n"
        
        message += "📋 **Основные команды:**\n"
        message += "• /start - Начать работу с ботом\n"
        message += "• /menu - Показать главное меню\n"
        message += "• /hide - Скрыть меню\n"
        message += "• /status - Проверить статус подключений\n"
        message += "• /get_token - Получить ссылку для нового VK токена\n"
        
        if user_info['is_admin']:
            message += "• /update_token - Обновить VK токен (админы)\n\n"
        else:
            message += "\n"
        
        message += "🎯 **Как опубликовать пост:**\n"
        message += "1. Нажмите '📢 Опубликовать пост'\n"
        message += "2. Выберите канал из списка\n"
        message += "3. Отправьте текст или фото\n"
        message += "4. Пост автоматически опубликуется в Telegram и VK\n\n"
        
        message += "📱 **Просмотр каналов:**\n"
        message += "• '📋 Мои каналы' - список доступных вам каналов\n\n"
        
        if user_info['is_admin']:
            message += "👥 **Функции администратора:**\n"
            message += "• '👥 Управление пользователями' - одобрение новых пользователей\n"
            message += "• '⚙️ Управление каналами' - просмотр и добавление каналов\n"
            message += "• /update_token - обновление VK токена\n\n"
        
        message += "❓ **Если у вас нет доступа к каналам или возникли проблемы - обратитесь к администратору.**"
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Публикация фото"""
        user = update.effective_user
        
        if not self.is_user_approved(user.id):
            await update.message.reply_text("❌ Доступ запрещен. Ожидайте одобрения администратора.")
            return
        
        if 'selected_channel' not in context.user_data:
            await update.message.reply_text("❌ Сначала выберите канал через меню")
            return
        
        channel = context.user_data['selected_channel']
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        
        try:
            # Скачиваем фото
            photo_file = await photo.get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            
            # Публикуем в Telegram
            await context.bot.send_photo(
                chat_id=channel['telegram'],
                photo=InputFile(BytesIO(photo_bytes), filename='photo.jpg'),
                caption=caption
            )
            
            # Публикуем в VK (если доступно)
            if self.check_vk_token():
                try:
                    photo_info = self.vk_upload.photo_wall(
                        photos=BytesIO(photo_bytes), 
                        group_id=channel['vk_group_id'].lstrip('-')
                    )[0]
                    
                    self.vk_api.wall.post(
                        owner_id=channel['vk_group_id'],
                        message=caption,
                        attachments=f"photo{photo_info['owner_id']}_{photo_info['id']}"
                    )
                    vk_status = "✅ Опубликовано в VK"
                except Exception as e:
                    logger.error(f"Ошибка публикации в VK: {e}")
                    vk_status = f"❌ Ошибка VK: {e}"
            else:
                vk_status = "⚠️ VK недоступен (токен истек)"
                if user_info := self.get_user(user.id):
                    if user_info['is_admin']:
                        vk_status += "\n🔧 Для получения нового токена: /get_token или /update_token"
            
            await update.message.reply_text(f"✅ Фото опубликовано в: {channel['name']}\n{vk_status}")
            
            # Очищаем выбранный канал после публикации
            context.user_data.pop('selected_channel', None)
            
        except Exception as e:
            logger.error(f"Ошибка публикации фото: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def publish_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Публикация текста"""
        channel = context.user_data['selected_channel']
        user = update.effective_user
        
        try:
            # Публикуем в Telegram
            await context.bot.send_message(
                chat_id=channel['telegram'],
                text=text
            )
            
            # Публикуем в VK (если доступно)
            if self.check_vk_token():
                try:
                    self.vk_api.wall.post(
                        owner_id=channel['vk_group_id'],
                        message=text
                    )
                    vk_status = "✅ Опубликовано в VK"
                except Exception as e:
                    logger.error(f"Ошибка публикации в VK: {e}")
                    vk_status = f"❌ Ошибка VK: {e}"
            else:
                vk_status = "⚠️ VK недоступен (токен истек)"
                if user_info := self.get_user(user.id):
                    if user_info['is_admin']:
                        vk_status += "\n🔧 Для получения нового токена: /get_token или /update_token"
            
            await update.message.reply_text(f"✅ Опубликовано в: {channel['name']}\n{vk_status}")
            
            # Очищаем выбранный канал после публикации
            context.user_data.pop('selected_channel', None)
            
        except Exception as e:
            logger.error(f"Ошибка публикации: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def admin_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Управление администраторами"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        keyboard = [
            ["👥 Список админов", "➕ Добавить админа"],
            ["🔙 Назад в меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "👑 Управление администраторами:",
            reply_markup=reply_markup
        )

    async def show_admins_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список администраторов"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT username, first_name FROM users WHERE is_admin = TRUE")
        admins = cursor.fetchall()
        conn.close()
        
        if not admins:
            message = "❌ Нет администраторов"
        else:
            message = "👑 Список администраторов:\n\n"
            for admin in admins:
                message += f"• {admin[1]} (@{admin[0]})\n"
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(message, reply_markup=reply_markup)

    async def start_add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало процесса добавления администратора"""
        user = update.effective_user
        user_info = self.get_user(user.id)
        
        if not user_info or not user_info['is_admin']:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        context.user_data['setup_stage'] = 'awaiting_admin_telegram_id'
        
        keyboard = [["🔙 Назад в меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "👑 Добавление администратора\n\n"
            "Введите Telegram ID пользователя:",
            reply_markup=reply_markup
        )
    
    def run(self):
        logger.info("Бот с Reply Keyboard и контролем доступа запущен...")
        self.tg_app.run_polling()

if __name__ == "__main__":
    bot = AdminControlledReplyBot()
    bot.run()
