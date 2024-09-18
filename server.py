import os
import json
import time
import requests
import logging
from flask import Flask, request, jsonify, url_for
from celeryconfig import Celery
from celery.result import AsyncResult

from celery import states
from celery.exceptions import Ignore

# Инициализация Flask
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
file_handler = logging.FileHandler('app.log')

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)

# Celery конфигурация
app.config['CELERY_BROKER_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
app.config['CELERY_RESULT_BACKEND'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                broker=app.config['CELERY_BROKER_URL'])

# Функция для выполнения запросов к ARI
def ari_request(method, endpoint, **kwargs):
    url = f"{BASE_URL}{endpoint}"
    try:
        response = requests.request(
            method, url, auth=(ASTERISK_USER, ASTERISK_PASSWORD), **kwargs
        )
        response.raise_for_status()
        logger.debug(f"Request {url} Method: {method} Payload: {kwargs}")
        logger.debug(f"Response {response.json()}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"ARI Request error: {e}")
        raise e

# Асинхронная задача для обработки вызова
@celery.task(bind=True)
def attended_transfer_task(self, internal_number, transfer_to_number, is_mobile):
    try:
        # Ищем активный канал для внутреннего номера
        logger.debug(f'Looking for active channel for {internal_number}')
        channels = ari_request('GET', '/channels')

        active_channel = next((channel for channel in channels 
                               if channel.get('caller', {}).get('number') == internal_number), None)

        if not active_channel:
            logger.error(f"Active call not found for {internal_number}")
            self.update_state(state=states.FAILURE, meta={'error': 'Active call not found'})
            raise Ignore()

        trunk_name = 'kazakhtelecom-out' if is_mobile else 'from-internal'
        
        # Инициализация исходящего вызова
        originate_data = {
            'endpoint': f'SIP/{trunk_name}/{transfer_to_number}' if is_mobile else f'SIP/{transfer_to_number}',
            'extension': transfer_to_number,
            'callerId': internal_number,
            'context': 'from-internal',
            'priority': 1
        }

        new_call = ari_request('POST', '/channels', json=originate_data)
        logger.debug(f'New call for transfer created: {new_call["id"]}')

        # Ожидание установки канала (до состояния Up)
        wait_for_channel_up(new_call['id'])

        # Создание моста (bridge) между двумя каналами
        bridge_data = {'type': 'mixing'}
        bridge = ari_request('POST', '/bridges', json=bridge_data)
        logger.debug(f'Bridge created {bridge["id"]}')

        # Добавляем каналы в мост
        ari_request('POST', f'/bridges/{bridge["id"]}/addChannel', 
                    json={'channel': [active_channel['id'], new_call['id']]})

        return {'status': 'success', 'message': f"Attended transfer to {transfer_to_number} completed"}

    except Exception as e:
        self.update_state(state=states.FAILURE, meta={'exc_type': str(type(e).__name__), 'exc_message': str(e)})
        raise e

# Ожидание состояния 'Up' для канала
def wait_for_channel_up(channel_id):
    while True:
        channel_response = ari_request('GET', f'/channels/{channel_id}')
        channel_state = channel_response['state']
        logger.debug(f'Channel {channel_id} state: {channel_state}')
        if channel_state == 'Up':
            break
        time.sleep(1)


# Маршрут для вызова асинхронного перевода через Celery
@app.route('/', methods=['get'])
def Default():
    return "OK"
@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    is_mobile = data.get('is_mobile', True)

    # Запуск асинхронной задачи перевода
    task = attended_transfer_task.apply_async(args=[internal_number, transfer_to_number, is_mobile])
    return jsonify({'task_id': task.id, 'status_url': url_for('task_status', task_id=task.id, _external=True)}), 202


# Маршрут для проверки статуса задачи в Celery
@app.route('/status/<task_id>')
def task_status(task_id):
    task = attended_transfer_task.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': task.state, 'status': 'Pending...'}
    elif task.state == 'FAILURE':
        response = {'state': task.state, 'error': str(task.info)}
    else:
        response = {'state': task.state, 'status': task.info}

    return jsonify(response)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=666, debug=False)