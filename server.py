import os
import json
import requests
import time
import logging
from flask import Flask, request, jsonify, url_for
from celery import Celery
from celery.result import AsyncResult

# Инициализация Flask-приложения
app = Flask(__name__)

# Настройки для подключения к Asterisk ARI (через переменные окружения)
ASTERISK_SERVER = os.getenv('AST_SERVER', '127.0.0.1')
ASTERISK_PORT = int(os.getenv('AST_PORT', 8088))
ASTERISK_USER = os.getenv('AST_USER', 'myuser')
ASTERISK_PASSWORD = os.getenv('AST_SECRET', 'mypassword')
APPLICATION = os.getenv('APPLICATION', 'hello-world')

BASE_URL = f"http://{ASTERISK_SERVER}:{ASTERISK_PORT}/ari"

# Настройки Celery и Redis
app.config['CELERY_BROKER_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
app.config['CELERY_RESULT_BACKEND'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Инициализация Celery
celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                broker=app.config['CELERY_BROKER_URL'])

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler('app.log')
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

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

# Ожидание поднятия канала до состояния 'Up'
def wait_for_channel_up(channel_id):
    while True:
        channel_response = ari_request('GET', f'/channels/{channel_id}')
        channel_state = channel_response['state']
        logger.debug(f'Channel {channel_id} state: {channel_state}')
        if channel_state == 'Up':
            break
        time.sleep(1)

# Определяем задачу для перевода вызовов (асинхронная задача в Celery)
@celery.task(bind=True)
def attended_transfer_task(self, internal_number, transfer_to_number, is_mobile):
    try:
        logger.debug(f'Looking for active call for internal number {internal_number}')
        
        # Получаем список каналов и проверяем наличие активного канала
        channels = ari_request('GET', '/channels')
        active_channel = next((channel for channel in channels 
                               if channel.get('caller', {}).get('number') == internal_number), None)
        
        if not active_channel:
            logger.error(f"No active call found for internal number {internal_number}")
            self.update_state(state='FAILURE', meta={'error': 'Active call not found'})
            return {'error': 'Active call not found'}

        trunk_name = 'kazakhtelecom-out' if is_mobile else 'from-internal'

        logger.debug(f"Initiating new call to {transfer_to_number} through trunk '{trunk_name}'")

        originate_data = {
            'endpoint': f'SIP/{trunk_name}/{transfer_to_number}',
            'extension': transfer_to_number,
            'callerId': internal_number,
            'context': 'from-internal',
            'priority': 1
        }

        # Инициируем звонок
        new_call = ari_request('POST', '/channels', json=originate_data)
        logger.debug(f'New call initiated: {new_call["id"]}')

        # Ожидаем, пока новый канал поднимется
        wait_for_channel_up(new_call['id'])

        # Создаем мост (bridge) и добавляем активные каналы в мост
        bridge_data = {'type': 'mixing'}
        bridge = ari_request('POST', '/bridges', json=bridge_data)
        logger.debug(f'Bridge created: {bridge["id"]}')
        
        # Добавляем оба канала в мост
        ari_request('POST', f'/bridges/{bridge["id"]}/addChannel',
                    json={'channel': [active_channel['id'], new_call['id']]})

        return {'status': 'success', 'message': f"Attended transfer to {transfer_to_number} completed"}

    except Exception as e:
        logger.error(f"Error during attended transfer: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise e

# Маршрут для запуска задачи перевода вызова через Celery
@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    is_mobile = data.get('is_mobile', True)

    logger.debug(f'Received attended transfer request: from {internal_number} to {transfer_to_number}')

    # Запуск задачи через Celery
    task = attended_transfer_task.apply_async(args=[internal_number, transfer_to_number, is_mobile])
    
    return jsonify({'task_id': task.id, 'status_url': url_for('task_status', task_id=task.id, _external=True)}), 202

# Маршрут для проверки статуса асинхронной задачи
@app.route('/status/<task_id>')
def task_status(task_id):
    task_result = celery.AsyncResult(task_id)
    if task_result.state == 'PENDING':
        response = {'state': task_result.state, 'status': 'Pending...'}
    elif task_result.state == 'FAILURE':
        response = {'state': task_result.state, 'error': str(task_result.info)}
    else:
        response = {'state': task_result.state, 'result': task_result.result}

    return jsonify(response)

# Дополнительный маршрут для отображения всех каналов
@app.route('/show_channels', methods=['GET'])
def show_channels():
    try:
        channels = ari_request('GET', '/channels')
        return jsonify(channels), 200
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    logger.debug("Starting Flask application on port 666")
    app.run(host="0.0.0.0", port=666, debug=False)