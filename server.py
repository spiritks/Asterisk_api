import os
import json
import requests
from flask import Flask, request, jsonify
import logging
import time
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
def wait_for_channel_up(channel_id):
            while True:
                channel_response = ari_request('GET', f'/channels/{channel_id}')
                channel_state = channel_response.json()['state']
                logger.debug(f'Chanel {channel_id} is {channel_state}')
                # Ждем пока канал не окажется в состоянии 'Up' или 'Ringing'
                if channel_state == 'Up':
                    #  or channel_state == 'Ringing'
                
                    break
                time.sleep(1)  # Ждем 1 секунду и повторяем запрос

@app.route('/originate', methods=['GET'])
def Originate():
    number_from = request.args.get('from',1000)
    number_to = request.args.get('to',302)
    originate_data = {
            'endpoint': f'SIP/{number_from}',
            'callerId': number_to,
            'extension': number_to,
            'context': 'from-internal',
            'priority': 1
        }
    logger.debug(f"trying to originate call to destination number with {originate_data}")
    new_call = ari_request(
            'POST', '/channels', json=originate_data
    ).json()
    logger.debug(f'Originate result {new_call}')
    return new_call
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
    is_mobile = data.get('is_mobile', True)  # Флаг, указывающий, является ли номер мобильным

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        # Шаг 1: Получить список активных каналов
        response = ari_request('GET', '/channels')
        channels = response.json()

        # Найти активный канал для абонента с указанным внутренним номером (internal_number)
        active_channel = None
        for channel in channels:
            if channel.get('caller', {}).get('number') == internal_number:
                active_channel = channel
                break

        if not active_channel:
            return jsonify({"error": "Active call not found for this internal number"}), 404
        trunk_name='kazakhtelecom-out'
        # Шаг 2: Инициализация нового звонка на transfer_to_number (в зависимости от типа номера)
        if is_mobile:
            # Формируем вызов на мобильный номер через SIP-транк
            originate_data = {
                'endpoint': f'SIP/{trunk_name}/{transfer_to_number}',  # Укажите имя вашего транка SIP
                'extension': transfer_to_number,
                'callerId': internal_number,
                'context': 'from-internal',
                'priority': 1
            }
        else:
            # Внутренний вызов
            originate_data = {
                'endpoint': f'SIP/{transfer_to_number}',
                'extension': transfer_to_number,
                'callerId': internal_number,
                'context': 'from-internal',
                'priority': 1
            }

        # Инициируем новый звонок на внутренний или мобильный номер
        new_call = ari_request(
            'POST', '/channels', json=originate_data
        ).json()

            # Шаг 3: Добавление каналов в bridge
        def on_new_call_stasis(event):
            bridging_data = {
                'channel': [active_channel['id'], new_call['id']]
            }
            ari_request(
                'POST', f"/bridges/{active_channel['id']}/addChannel", json=bridging_data
            )

        # Ждём, пока новый вызов поднимется и завершаем перевод
        wait_for_channel_up(new_call['id'])
        on_new_call_stasis(new_call)

        return jsonify({"success": True, "message": f"Attended transfer to {transfer_to_number} completed"})

    except requests.exceptions.HTTPError as err:
        return jsonify({"error": str(err)}), 500


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=666, debug=False)