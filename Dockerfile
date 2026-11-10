FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код
COPY . .

# Сообщения бота (логи) и данные пользователей
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
