# Указываем базовый образ с Python
FROM python:latest

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем необходимые библиотеки (Flask, pyst2 и другие)
RUN apt-get update -y
RUN apt-get install -y iputils-ping wget
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения в контейнер
COPY . .

# Открываем порт, который используется Flask
EXPOSE 666

# Запускаем приложение через gunicorn с количеством рабочих процессов (например, 4)
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:666", "server:app"]