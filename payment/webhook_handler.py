import logging
import os

import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify

app = Flask(__name__)

load_dotenv()
# Настройки
BOT_TOKEN = os.getenv('BOT_TOKEN_PROD')
N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL')

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def send_success_message(user_id, question, tariff):
    """Отправляем уведомление об успешной оплате"""
    try:
        from telegram import Bot
        import asyncio

        async def async_send():
            bot = Bot(token=BOT_TOKEN)
            message = (
                "✅ *Оплата прошла успешно!*\n\n"
                f"🔮 *Ваш вопрос:* {question}\n"
                f"💎 *Тариф:* {'1 обращение' if tariff == 'single' else 'пакет 6 обращений'}\n\n"
                "Оракул уже приступил к разбору вашей ситуации. "
                "Ответ придет в этом чате в течение 1-2 минут.\n\n"
                "Благодарю за доверие! 💫"
            )
            await bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')

        asyncio.run(async_send())
        logger.info(f"✅ Уведомление об оплате отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления: {e}")

def forward_to_n8n(webhook_data):
    """Пересылаем вебхук в n8n для обработки"""
    try:
        response = requests.post(N8N_WEBHOOK_URL, json=webhook_data, timeout=30)
        if response.status_code == 200:
            logger.info("✅ Вебхук успешно передан в n8n")
            return True
        else:
            logger.error(f"❌ Ошибка n8n: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к n8n: {e}")
        return False

@app.route('/webhook/yookassa', methods=['POST'])
def handle_yookassa_webhook():
    """Обработчик вебхуков от ЮKassa"""
    try:
        data = request.get_json()
        logger.info(f"📨 Получен вебхук от ЮKassa: {data.get('event')}")

        if data.get('event') == 'payment.succeeded':
            payment = data['object']
            metadata = payment.get('metadata', {})

            user_id = metadata.get('user_id')
            question = metadata.get('question', 'вопрос не указан')
            tariff = metadata.get('tariff', 'single')

            if user_id:
                # 1. Сначала отправляем быстрое уведомление
                send_success_message(user_id, question, tariff)

                # 2. Затем пересылаем в n8n для генерации ответа
                forward_to_n8n(data)

                logger.info(f"🎉 Обработана оплата от пользователя {user_id}")
            else:
                logger.warning("⚠️ user_id не найден в metadata")

        return jsonify({'status': 'success'}), 200

    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return jsonify({'status': 'error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    logger.info("🌐 Вебхук-сервер запущен на порту 5000")
    app.run(host='0.0.0.0', port=5000)