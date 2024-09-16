import os
import json
import requests
from flask import Flask, request, jsonify
import logging
app = Flask(__name__)

# Получаем необходимые данные для подключения к Asterisk ARI (через переменные окружения)
ASTERISK_SERVER = os.getenv('AST_SERVER', '127.0.0.1')
ASTERISK_PORT = int(os.getenv('ASTERISK_PORT', 8088))
ASTERISK_USER = os.getenv('AST_USER', 'myuser')
ASTERISK_PASSWORD = os.getenv('AST_SECRET', 'mypassword')
APPLICATION = os.getenv('APPLICATION', 'hello-world')

BASE_URL = f"http://{ASTERISK_SERVER}:{ASTERISK_PORT}/ari"
# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Логирование в файл
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Функция для выполнения запроса к ARI
def ari_request(method, endpoint, **kwargs):
    url = f"{BASE_URL}{endpoint}"
    response = requests.request(
        method, url, auth=(ASTERISK_USER, ASTERISK_PASSWORD), **kwargs
    )
    response.raise_for_status()  # Поднимаем исключение в случае ошибки HTTP
    logger.debug(f"request {url} {method} {jsonify(**kwargs)}")
    logger.debug(f"Response {response.json()}")
    return response
@app.route('/show_channels', methods=['GET'])
def show_channels():
    try:
        response = ari_request('GET', '/channels')
        channels_data = response.json()
        return jsonify(channels_data), 200  # Возвращаем данные каналов и успешный статус
    except requests.exceptions.HTTPError as err:
        return jsonify({"error": str(err)}), 500  # Если ошибка - показываем её

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        # Шаг 1: Получаем список активных каналов
        response = ari_request('GET', '/channels')
        channels = response.json()

        # Находим активный канал для абонента с указанным номером
        active_channel = None
        for channel in channels:
            if channel.get('caller', {}).get('number') == internal_number:
                active_channel = channel
                break

        if not active_channel:
            return jsonify({"error": "Active call not found for this internal number"}), 404
        
        # Шаг 2: Инициализируем новый вызов на transfer_to_number
        originate_data = {
            'endpoint': f'SIP/{transfer_to_number}',
            'extension': transfer_to_number,
            'callerId': internal_number,
            'context': 'from-internal',
            'priority': 1
        }

        new_call = ari_request(
            'POST', '/channels', json=originate_data
        ).json()

        # Шаг 3: Создаём новый бридж и добавляем в него оба канала как только второй поднят
        bridge_data = {
            'type': 'mixing'  # Тип моста
        }
        bridge = ari_request('POST', '/bridges', json=bridge_data).json()

        # Добавляем оба канала в бридж
        ari_request(
            'POST', f"/bridges/{bridge['id']}/addChannel", json={'channel': [active_channel['id'], new_call['id']]}
        )

        return jsonify({"success": True, "message": f"Attended transfer to {transfer_to_number} completed"})

    except requests.exceptions.HTTPError as err:
        return jsonify({"error": str(err)}), 500
    

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=666, debug=False)