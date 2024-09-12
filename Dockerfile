# Указываем базовый образ с Python
FROM python:3.9-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем необходимые библиотеки (Flask, pyst2 и другие)
RUN apt-get update -y
RUN apt-get install -y iputils-ping
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения в контейнер
COPY . .

# Открываем порт, который используется Flask
EXPOSE 5000

# Запускаем приложение
CMD ["python", "server.py"]