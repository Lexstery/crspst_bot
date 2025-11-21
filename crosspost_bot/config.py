import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
VK_TOKEN = os.getenv('VK_TOKEN') 
VK_GROUP_ID = os.getenv('VK_GROUP_ID')