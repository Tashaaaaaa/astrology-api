# Используем официальный Python-образ как базу
FROM python:3.11-slim

# Устанавливаем системные зависимости, нужные для pyswisseph
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libswisseph-dev \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем Python-пакеты
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект внутрь контейнера
COPY . .

# Открываем порт 8000 для доступа к серверу
EXPOSE 8000

# Команда, которая запускает сервер
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
