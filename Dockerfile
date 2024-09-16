# Указываем базовый образ с Python (alpine является легковесным образом)
FROM python:3.9-alpine3.20

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# # Обновляем Alpine, удаляем libressl-dev и устанавливаем необходимые пакеты
# RUN apk update && \
#     apk del libressl-dev && \
#     apk add --no-cache \
#     git \
#     gcc \
#     musl-dev \
#     linux-headers \
#     libffi-dev \
#     openssl-dev \
#     bash

# # Клонируем репозиторий ari-py
# RUN git clone https://github.com/asterisk/ari-py /tmp/ari-py

# # Применяем команду "sed" для нахождения всех упоминаний urlparse и замены их на urllib.parse
# RUN sed -i 's/import urlparse/from urllib.parse import urlparse, urljoin/' /tmp/ari-py/ari/client.py

# # Устанавливаем исправленную версию
# RUN pip install /tmp/ari-py

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем остальные зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем всё приложение
COPY . .

# Открываем необходимый порт Flask
EXPOSE 666

# Запускаем приложение
CMD [ "python", "server.py" ]