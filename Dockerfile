# Указываем базовый образ с Python
FROM python:alpine3.20

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем необходимые библиотеки (Flask, pyst2 и другие)
# RUN apt-get update -y
# RUN apt-get install -y iputils-ping wget telnet
# RUN pip install git+https://github.com/ettoreleandrotognoli/python-ami
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения в контейнер
# COPY . .

# Открываем порт, который используется Flask
EXPOSE 666

# Запускаем приложение через gunicorn с количеством рабочих процессов (например, 4)
CMD [ "python","server.py" ]
# CMD ["gunicorn", "-w", "6", "-b","0.0.0.0:666", "-t","360", "server:app"]