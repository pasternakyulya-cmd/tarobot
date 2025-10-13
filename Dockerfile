FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
RUN pip install --no-cache-dir python-telegram-bot

# Копируем код приложения
COPY . .

# Команда запуска
CMD ["python", "bot.py"]