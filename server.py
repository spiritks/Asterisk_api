import os
import json
import time
import requests
import logging
from flask import Flask, request, jsonify, url_for,Response
from celery import Celery
import subprocess
# Инициализация Flask-приложения
app = Flask(__name__)

# Настройки для подключения к Asterisk ARI (через переменные окружения)
ASTERISK_SERVER = os.getenv('AST_SERVER', '127.0.0.1')
ASTERISK_PORT = int(os.getenv('AST_PORT', 8088))
ASTERISK_USER = os.getenv('AST_USER', 'myuser')
ASTERISK_PASSWORD = os.getenv('AST_SECRET', 'mypassword')

BASE_URL = f"http://{ASTERISK_SERVER}:{ASTERISK_PORT}/ari"

# Настройки Celery и Redis
app.config['CELERY_BROKER_URL'] = os.getenv('REDIS_URL', 'redis://redis:6379/0')
app.config['CELERY_RESULT_BACKEND'] = os.getenv('REDIS_URL', 'redis://redis:6379/0')

# Инициализация Celery с конфигурацией Flask
celery = Celery(app.import_name, backend=app.config['CELERY_RESULT_BACKEND'],
                broker=app.config['CELERY_BROKER_URL'])

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler('app.log')
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Функция для выполнения запросов к ARI (Asterisk)
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


# Задача для перевода вызова (асинхронная задача в Celery)
@celery.task(bind=True, name='app.attended_transfer_task')
def attended_transfer_task(self, internal_number, transfer_to_number, is_mobile):
    try:
        logger.debug(f'Looking for active call for internal number {internal_number}')
        
        channels = ari_request('GET', '/channels')  # Получаем список всех каналов
        active_channel = next((channel for channel in channels 
                               if channel.get('caller', {}).get('number') == internal_number), None)
        
        if not active_channel:
            logger.error(f"No active call found for number {internal_number}")
            raise ValueError('Active call not found')

        trunk_name = 'kazakhtelecom-out' if is_mobile else 'from-internal'

        logger.debug(f"Initiating new call to {transfer_to_number} through trunk '{trunk_name}'")

        originate_data = {
            'endpoint': f'SIP/{trunk_name}/{transfer_to_number}',
            'extension': transfer_to_number,
            'callerId': internal_number,
            'context': 'from-internal',
            'priority': 1
        }

        new_call = ari_request('POST', '/channels', json=originate_data)
        logger.debug(f'New call initiated: {new_call["id"]}')

        wait_for_channel_up(new_call['id'])

        # Создаем мост для соединения обоих каналов
        bridge_data = {'type': 'mixing'}
        bridge = ari_request('POST', '/bridges', json=bridge_data)
        logger.debug(f'Bridge created: {bridge["id"]}')
        
        ari_request('POST', f'/bridges/{bridge["id"]}/addChannel',
                    json={'channel': [active_channel['id'], new_call['id']]})

        return {'status': 'success', 'message': f"Attended transfer to {transfer_to_number} completed"}

    except ValueError as e:
        logger.error(f"Error in attended transfer for {internal_number} -> {transfer_to_number}: {str(e)}")
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)}) # Исправляем, чтобы отмечать задачу проваленной
        raise e  # Передаём реальное исключение для правильной сериализации
    except Exception as e:
        logger.error(f"General error in task: {str(e)}")
        self.update_state(
        state='FAILURE', 
        meta={'exc_type': type(e).__name__, 'exc_message': str(e)}
    )

        
        raise e
def send_dtmf_signals(channel_id, dtmf_sequence):
    dtmf_data = {
        'dtmf': dtmf_sequence,
        'before': 0,    # Время до передачи сигнала
        'duration': 100 # Длина сигнала
    }
    return ari_request('POST', f'/channels/{channel_id}/dtmf', json=dtmf_data).json()


@app.route('/api/dtmf_transfer', methods=['POST'])
def dtmf_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    is_mobile = data.get('is_mobile', True)
    channels = ari_request('GET', '/channels')  # Получаем список всех каналов
    active_channel = next((channel for channel in channels 
        if channel.get('caller', {}).get('number') == internal_number), None)
        
    if not active_channel:
            logger.error(f"No active call found for number {internal_number}")
            raise ValueError('Active call not found')
    return send_dtmf_signals(active_channel['id'],f"*2{transfer_to_number}").json()
    
# Маршрут для запуска задачи
@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    is_mobile = data.get('is_mobile', True)

    logger.debug(f"Attended transfer request from {internal_number} to {transfer_to_number}")

    # Запуск задачи через Celery
    task = attended_transfer_task.apply_async(args=[internal_number, transfer_to_number, is_mobile])
    
    return jsonify({'task_id': task.id, 'status_url': url_for('task_status', task_id=task.id, _external=True)}), 202

@app.route('/originate', methods=['GET'])
def Originate():
    number_from = request.args.get('from',1000)
    number_to = request.args.get('to',302)
    originate_data = {
            'endpoint': f'SIP/kazakhtelecom-out/{number_to}',
            'callerId': number_to,
            'extension': number_from,
            'context': 'from-internal',
            'priority': 1
        }
    logger.debug(f"trying to originate call to destination number with {originate_data}")
    new_call = ari_request(
            'POST', '/channels', json=originate_data
    )
    logger.debug(f'Originate result {new_call}')
    return jsonify(new_call)

@app.route('/show_channels', methods=['GET'])
def show_channels():
    try:
        response = ari_request('GET', '/channels')
        channels_data = response.json()
        return jsonify(channels_data), 200  # Возвращаем данные каналов и успешный статус
    except requests.exceptions.HTTPError as err:
        return jsonify({"error": str(err)}), 500  # Если ошибка - показываем её

# Маршрут для проверки статуса задачи
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

@app.route('/logs/app', methods=['GET'])
def get_app_logs():
    LOG_FILE_PATH="app.log"
    try:
        # Открываем и читаем файл логов
        with open(LOG_FILE_PATH, 'r') as log_file:
            logs = log_file.readlines()
        return Response(logs, mimetype='text/plain'), 200  # Возвращаем содержимое как текст
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return jsonify({"error": f"Could not read app.log: {str(e)}"}), 500


# -----------------------------------
# Endpoint для отображения docker-compose logs
# -----------------------------------
@app.route('/logs/compose', methods=['GET'])
def get_docker_logs():
    try:
        # Выполняем команду `docker-compose logs` для получения всех Docker-логов
        result = subprocess.run(["docker-compose", "logs"], capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Error executing docker-compose logs: {result.stderr}")
            return jsonify({"error": result.stderr.strip()}), 500

        return Response(result.stdout, mimetype='text/plain'), 200  # Выводим логи в браузер
    except Exception as e:
        logger.error(f"Error executing docker-compose logs: {e}")
        return jsonify({"error": f"Could not run docker-compose logs: {str(e)}"}), 500
if __name__ == '__main__':
    logger.debug("Starting Flask application")
    app.run(host="0.0.0.0", port=666, debug=False)