import os

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID')
    YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY')