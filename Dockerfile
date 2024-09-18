# Базовый образ Python
FROM python:3.9-alpine3.20

# Устанавливаем переменные окружения и рабочую директорию приложения
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Устанавливаем зависимости системы
RUN apk update && apk add --no-cache \
    build-base \
    musl-dev \
    linux-headers \
    gcc \
    g++ \
    libffi-dev \
    openssl-dev \
    bash \
    redis \
    && pip install --upgrade pip

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения в рабочую директорию
COPY . .

# Открываем порт сервера Flask
EXPOSE 666

# Запуск Redis внутри контейнера (опционально)
# Запустим приложение через gunicorn из файла "server.py"
# CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:666", "--timeout", "360", "server:app"]
CMD ["python","server.py"]