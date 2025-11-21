import logging
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from vk_api import VkApi
from vk_api.upload import VkUpload
from io import BytesIO
import sqlite3

from config import TELEGRAM_TOKEN, VK_TOKEN, VK_GROUP_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ButtonCrossPostBot:
    def __init__(self):
        self.tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.setup_handlers()
        
        # Инициализируем VK
        try:
            self.vk_session = VkApi(token=VK_TOKEN)
            self.vk_api = self.vk_session.get_api()
            self.vk_upload = VkUpload(self.vk_session)
            logger.info("✅ VK API инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка VK: {e}")
            self.vk_api = None
    
    def setup_handlers(self):
        self.tg_app.add_handler(CommandHandler("start", self.start_command))
        self.tg_app.add_handler(CommandHandler("channels", self.channels_command))
        self.tg_app.add_handler(CallbackQueryHandler(self.button_handler))
        self.tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.tg_app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await update.message.reply_text(
            f"Привет, {user.first_name}! 👋\n\n"
            "Я умный бот для кросспостинга!\n\n"
            "Команды:\n"
            "/channels - Выбрать канал для публикации\n\n"
            "Сначала выбери канал, потом отправляй контент! 🚀"
        )
    
    async def channels_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показываем кнопки с выбором каналов"""
        
        # Создаем клавиатуру с кнопками
        keyboard = [
            [InlineKeyboardButton("Тестовый канал", callback_data="tch")],
            [InlineKeyboardButton("Тестовый канал 1", callback_data="tch1")],
            [InlineKeyboardButton("Тестовый канал 2", callback_data="tch2")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎯 Выбери канал для публикации:",
            reply_markup=reply_markup
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        # Сохраняем выбранный канал в контексте пользователя
        if data == "tch":
            context.user_data['selected_channel'] = {
                'name': 'Тестовый канал',
                'telegram': '@testcrspst',  # ЗАМЕНИ НА СВОЙ
                'vk_group_id': '-191512637'  # ← ДОБАВЬ ID ГРУППЫ ДЛЯ НОВОСТЕЙ
            }
            await query.edit_message_text("✅ Выбран: Тестовый канал")
            
        elif data == "tch1":
            context.user_data['selected_channel'] = {
                'name': 'Тестовый канал 1', 
                'telegram': '@testcrspst1',  # ЗАМЕНИ НА СВОЙ
                'vk_group_id': '-234060559'  # ← ДОБАВЬ ID ГРУППЫ ДЛЯ АКЦИЙ
            }
            await query.edit_message_text("✅ Выбран: Тестовый канал 1")
            
        elif data == "tch2":
            context.user_data['selected_channel'] = {
                'name': 'Тестовый канал 2',
                'telegram': '@testcrspst2',  # ЗАМЕНИ НА СВОЙ
                'vk_group_id': '-234060576'  # ← ДОБАВЬ ID ГРУППЫ ДЛЯ ТЕХ БЛОГА
            }
            await query.edit_message_text("✅ Выбран: Тестовый канал 2")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        
        
        # Проверяем, выбран ли канал
        if 'selected_channel' not in context.user_data:
            await update.message.reply_text(
                "❌ Сначала выбери канал командой /channels"
            )
            return
        
        channel = context.user_data['selected_channel']
        text = update.message.text
        
        try:
            # Публикуем в Telegram
            await context.bot.send_message(
                chat_id=channel['telegram'],
                text=text
            )
            
            # Публикуем в VK
            if self.vk_api:
                self.vk_api.wall.post(
                    owner_id=channel['vk_group_id'],
                    message=text
                )
            
            await update.message.reply_text(
                f"✅ Опубликовано в: {channel['name']} 📱"
            )
            
        except Exception as e:
            logger.error(f"Ошибка публикации: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        
        
        if 'selected_channel' not in context.user_data:
            await update.message.reply_text(
                "❌ Сначала выбери канал командой /channels"
            )
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
            
            # Публикуем в VK
            if self.vk_api:
                photo_info = self.vk_upload.photo_wall(
                    photos=BytesIO(photo_bytes), 
                    group_id=channel['vk_group_id'].lstrip('-')
                )[0]
                
                self.vk_api.wall.post(
                    owner_id=channel['vk_group_id'],
                    message=caption,
                    attachments=f"photo{photo_info['owner_id']}_{photo_info['id']}"
                )
            
            await update.message.reply_text(
                f"✅ Фото опубликовано в: {channel['name']} 📱"
            )
            
        except Exception as e:
            logger.error(f"Ошибка публикации фото: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    def run(self):
        logger.info("Бот с кнопками запущен...")
        self.tg_app.run_polling()

if __name__ == "__main__":
    bot = ButtonCrossPostBot()
    bot.run()