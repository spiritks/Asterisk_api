import os
import json
import time
import socket
import logging
from flask import Flask, request, jsonify, url_for, Response
from celery import Celery
import subprocess
from dotenv import load_dotenv  # Импортируем dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Инициализация Flask-приложения
app = Flask(__name__)

# Настройки для подключения к Asterisk AMI (через переменные окружения)
AMI_HOST = os.getenv('AMI_HOST', '127.0.0.1')
AMI_PORT = int(os.getenv('AMI_PORT', 5038))
AMI_USER = os.getenv('AMI_USER', 'admin')
AMI_PASSWORD = os.getenv('AMI_PASSWORD', 'password')

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

# Функция для отправки команды в Asterisk AMI через сокет
def send_ami_command(command):
    sock = None
    try:
        # Подключаемся к Asterisk AMI
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((AMI_HOST, AMI_PORT))
        
        # Логинимся
        login_command = (
            f'Action: Login\r\n'
            f'Username: {AMI_USER}\r\n'
            f'Secret: {AMI_PASSWORD}\r\n'
            f'Events: off\r\n\r\n'
        )
        # Отправляем команду логина
        sock.sendall(login_command.encode())
        
        # Функция для получения полного ответа
        def recv_response():
            response = ''
            while True:
                data = sock.recv(1024).decode()
                response += data
                if '\r\n\r\n' in data:
                    break
            return response
        
        # Получаем ответ на логин
        response = recv_response()
        if 'Response: Success' not in response:
            logger.error(f'AMI login failed! {response}')
            return None
        
        # Отправляем нашу команду
        sock.sendall(command.encode())

        # Теперь читаем ответ на команду ПОСТОЯННО, пока не получим EventList: Complete
        full_response = ''
        shirtreq = True
        while True:
            chunk = sock.recv(1024).decode()
            if "will follow" in chunk:
                shirtreq = False
            full_response += chunk
            
            if 'EventList: Complete' in chunk or shirtreq:  # Завершение списка событий
                break

        return full_response
    except Exception as e:
        logger.error(f"AMI command failed: {e}")
        return None
    finally:
        if sock:
            sock.close()
            
# import time  # Для отслеживания времени
# import logging

# logger = logging.getLogger(__name__)
def parse_core_show_channels_response(response):
    """Функция для обработки ответа от CoreShowChannels и сбора всех каналов в список."""
    channels = []
    channel_info = {}
    
    # Разбираем ответ построчно
    for line in response.splitlines():
        if line.startswith('Event: CoreShowChannel'):
            # Когда начинаем новый канал, возможно, нужно сохранить предыдущие данные
            if channel_info:
                channels.append(channel_info)
            channel_info = {}  # Новый словарь для следующего канала

        # Собираем данные для каждого канала
        if "Channel: " in line:
            channel_info['Channel'] = line.split(': ', 1)[1].strip()
        if "CallerIDNum: " in line:
            channel_info['CallerIDNum'] = line.split(': ', 1)[1].strip()
        if "ChannelStateDesc: " in line:
            channel_info['ChannelStateDesc'] = line.split(': ', 1)[1].strip()
        if "Linkedid: " in line:
            channel_info['Linkedid'] = line.split(': ', 1)[1].strip()
        if "BridgeId: " in line:
            channel_info['BridgeId'] = line.split(': ', 1)[1].strip()
    
    # Добавляем последний канал в список (если информация по нему есть)
    if channel_info:
        channels.append(channel_info)

    return channels

def find_active_channel(internal_number, channels):
    """Функция для нахождения активного канала инициатора."""
    active_channel = None

    logger.debug(f"# Ищем активный канал инициатора (A) для CallerIDNum: {internal_number}")

    # Поиск нужного канала из предварительно собранного списка
    for channel_info in channels:
        caller_id = channel_info.get('CallerIDNum')
        channel_state = channel_info.get('ChannelStateDesc')
        channel_id = channel_info.get('Channel')
        if caller_id == internal_number and channel_state == "Up":
            logger.debug(f"Канал {channel_id} с CallerID {internal_number} активен и имеет статус 'Up'")
            active_channel = channel_id
            break

    if active_channel:
        logger.debug(f"Найден активный канал инициатора: {active_channel}")
    else:
        logger.error(f"Не удалось найти активный канал инициатора {internal_number}")

    return active_channel

# Функция для отправки команды Atxfer
def atxfer_call(active_channel, transfer_to_number, target_context):
    atxfer_command = (
        f'Action: Atxfer\r\n'
        f'Channel: {active_channel}\r\n'        # Канал инициатора
        f'Exten: {transfer_to_number}\r\n'      # Целевой номер (номер абонента C)
        f'Context: {target_context}\r\n'        # Контекст для перевода звонка
        f'\r\n'
    )

    logger.debug(f"Sending Atxfer command to transfer call to {transfer_to_number}")
    atxfer_response = send_ami_command(atxfer_command)
    logger.debug(f"Atxfer response: {atxfer_response}")

    if 'Response: Success' not in atxfer_response:
        logger.error(f"Failed to transfer call: {atxfer_response}")
        raise ValueError("Atxfer command failed")
    
    logger.debug(f"Call successfully transferred to {transfer_to_number}")
    return {'status': 'success', 'message': f"Call transferred to {transfer_to_number}"}

# Пример функции перевода вызова с отслеживанием статуса
@celery.task(bind=True, name='app.attended_transfer_task')
def attended_transfer_task(self, internal_number, transfer_to_number, is_mobile):
    try:
        if is_mobile:
            transfer_to_number = f"8{transfer_to_number}"
        # 1. Найти активный канал инициатора через CoreShowChannels
        active_channel = find_active_channel (internal_number)
        if not active_channel:
            logger.error(f"No active call found for number {internal_number}. Aborting transfer.")
            raise ValueError(f"No active call found for initiator {internal_number}")

        # 2. Инициализация перевода via Atxfer
        target_context = 'from-internal'  # Контекст для диалинга
        transfer_status = atxfer_call(active_channel, transfer_to_number, target_context)
        logger.debug(f"Transfer status: {transfer_status}")
        

    except ValueError as e:
        logger.error(f"Error during attended transfer: {str(e)}")
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e
    except Exception as e:
        logger.error(f"General error during attended transfer task: {str(e)}")
        self.update_state(
            state='FAILURE', 
            meta={'exc_type': type(e).__name__, 'exc_message': str(e)},
        )
        raise e


# Маршрут для запуска задачи attended_transfer
@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    is_mobile = data.get('is_mobile', True)
    if len(transfer_to_number)>3:
        is_mobile=True

    logger.debug(f"Attended transfer request from {internal_number} to {transfer_to_number}")

    # Запуск задачи через Celery
    task = attended_transfer_task.apply_async(args=[internal_number, transfer_to_number, is_mobile])
    
    return jsonify({'task_id': task.id, 'status_url': url_for('task_status', task_id=task.id, _external=True)}), 202

# Маршрут для отправки DTMF тонов через AMI
@app.route('/api/dtmf_transfer', methods=['POST'])
def dtmf_transfer():
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')
    
    logger.debug(f"Sending DTMF *2{transfer_to_number} to {internal_number}")

    # Находим активный канал для внутреннего номера
    channels_response = send_ami_command('Action: CoreShowChannels\r\n\r\n')
    if not channels_response:
        return jsonify({"error": "Cannot retrieve channels"}), 500

    active_channel = None
    for line in channels_response.splitlines():
        if f"CallerIDNum: {internal_number}" in line:
            active_channel = line.split()[1]  # Предполагаем формат строки "Channel: CHANNEL_ID"

    if not active_channel:
        return jsonify({"error": f"No active call found for number {internal_number}"}), 404
    
    # Отправляем DTMF команду через AMI
    dtmf_command = (
        f'Action: PlayDTMF\r\n'
        f'Channel: {active_channel}\r\n'
        f'DTMF: *2{transfer_to_number}\r\n\r\n'
    )
    response = send_ami_command(dtmf_command)

    if 'Response: Success' in response:
        return jsonify({'status': 'success', 'message': f"DTMF transfer *2{transfer_to_number} sent to {internal_number}"}), 200
    else:
        return jsonify({'status': 'failure', 'message': response}), 500

# Маршрут для отображения каналов (CoreShowChannels)
@app.route('/show_channels', methods=['GET'])
def show_channels():
    response = send_ami_command('Action: CoreShowChannels\r\n\r\n')
    return Response(response, mimetype='text/plain'), 200

# Route for Celery task status check
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
        with open(LOG_FILE_PATH, 'r') as log_file:
            logs = log_file.readlines()
        return Response(logs, mimetype='text/plain'), 200
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return jsonify({"error": f"Could not read app.log: {str(e)}"}), 500

@app.route('/originate_call', methods=['POST'])
def originate_call():
    # Получаем параметры для Originate из запроса
    data = request.json
    from_number = data.get('from')
    to_number = data.get('to')
    context = data.get('context', 'from-internal')  # Контекст по умолчанию
    caller_id = data.get('caller_id', from_number)  # ID звонящего (по умолчанию тот же номер)
    priority = data.get('priority', 1)  # Приоритет по умолчанию

    if not from_number or not to_number:
        return jsonify({"error": "Missing 'from' or 'to' parameter"}), 400
    trunk_name = 'kazakhtelecom-out'

    logger.debug(f"Initiating new call to {to_number} through trunk '{trunk_name}'")

    # Формируем команду Originate для AMI
    originate_command = (
        f'Action: Originate\r\n'
        f'Channel: SIP/kazakhtelecom-out/{to_number}\r\n'  # Внешний номер через SIP-транк
        f'Exten: {from_number}\r\n'                        # Внутренний номер (куда звонить в контексте)
        f'Context: {context}\r\n'                          # Контекст диалплана (например, from-internal)
        f'Priority: {priority}\r\n'                        # Приоритет в диалплане
        f'CallerID: {caller_id}\r\n'                       # CallerID звонящего (например, 1000)
        f'Async: true\r\n'                                 # Асинхронный вызов
        f'\r\n'
    )
    # Команда Originate для AMI
    # originate_command = (
    #     f'Action: Originate\r\n'
    #     f'Channel: SIP/{from_number}\r\n'  # Канал для выхода (например, через SIP)
    #     f'Exten: {to_number}\r\n'          # Куда совершается вызов
    #     f'Context: {context}\r\n'          # Контекст в Asterisk диалплане
    #     f'Priority: {priority}\r\n'        # Приоритет вызова
    #     f'CallerID: {caller_id}\r\n'       # Идентификатор звонящего (CallerID)
    #     f'Async: true\r\n\r\n'             # Асинхронное выполнение
    # )

    # Отправляем команду на AMI
    response = send_ami_command(originate_command)

    if response and 'Response: Success' in response:
        return jsonify({"status": "success", "message": "Call initiated successfully"})
    else:
        logger.error(f"Failed to initiate call: {response}")
        return jsonify({"error": "Failed to initiate call", "response": response}), 500
@app.route('/listbridges', methods=['get'])
def listBridges():
    bridges_response = send_ami_command('Action: BridgeList\r\n\r\n')
    return bridges_response.splitlines()
if __name__ == '__main__':
    logger.debug("Starting Flask application")
    app.run(host="0.0.0.0", port=666, debug=False)