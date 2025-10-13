FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
#COPY requirements.txt .
#RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Команда запуска
CMD ["python", "bot.py"]