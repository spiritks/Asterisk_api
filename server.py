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
        # logger.info(login_command.encode())
        sock.sendall(login_command.encode())
        
        # Чтение ответа от AMI
        def recv_response():
            response = ''
            while True:
                data = sock.recv(1024).decode()
                response += data
                if '\r\n\r\n' in data:
                    break
            return response
        
        # Получаем ответ на login
        response = recv_response()
        if 'Response: Success' not in response:
            logger.error(f'AMI login failed! {response}')

            return None

        # Отправляем команду
        logger.debug(f"Sending AMI command: {command}")
        sock.sendall(command.encode())
        
        # Получаем ответ на нашу команду
        response = recv_response()
        logger.debug(f"AMI Response: {response}")
        
        return response
    except Exception as e:
        logger.error(f"AMI command failed: {e}")
        return None
    finally:
        if sock:
            sock.close()

@celery.task(bind=True, name='app.attended_transfer_task')
def attended_transfer_task(self, internal_number, transfer_to_number, is_mobile):
    try:
        logger.debug(f'Starting attended transfer for {internal_number} to {transfer_to_number}')

        trunk_name = 'kazakhtelecom-out' if is_mobile else 'from-internal'

        # 1. Создаем мост (Bridge)
        logger.debug("Creating a new bridge")
        bridge_command = (
            f'Action: BridgeCreate\r\n'
            f'Type: mixing\r\n'  # Миксирование, позволяет обоим каналам разговаривать
            f'BridgeType: mixing\r\n'
            f'\r\n'
        )

        bridge_response = send_ami_command(bridge_command)

        # Проверяем успешность создания моста
        if 'Response: Success' not in bridge_response:
            logger.error(f"Failed to create bridge: {bridge_response}")
            raise ValueError("Bridge creation failed")

        # Получаем ID созданного моста
        bridge_id = None
        for line in bridge_response.splitlines():
            if line.startswith("Bridge"):
                bridge_id = line.split(":")[1].strip()

        if not bridge_id:
            logger.error("Bridge ID not found after creation")
            raise ValueError("Bridge ID retrieval failed")
        
        logger.debug(f'Created bridge with ID: {bridge_id}')

        # 2. Инициируем вызов на целевой номер (B)
        logger.debug(f'Initiating call to {transfer_to_number} through trunk {trunk_name}')
        
        originate_command = (
            f'Action: Originate\r\n'
            f'Channel: SIP/{trunk_name}/{transfer_to_number}\r\n'  # Вызов на целевой номер
            f'Context: from-internal\r\n'                          # Контекст диалплана
            f'Exten: {transfer_to_number}\r\n'                     # Важный параметр, куда идет вызов
            f'Priority: 1\r\n'                                     # Приоритет обработки диалплана
            f'Async: true\r\n'                                     # Вызов будет выполен асинхронно
            f'\r\n'
        )

        originate_response = send_ami_command(originate_command)
        logger.debug(f"Originate response: {originate_response}")

        # Проверяем успешность выполнения команды Originate
        if 'Response: Success' not in originate_response:
            logger.error(f"Failed to initiate call: {originate_response}")
            raise ValueError("Failed to originate call")

        # Ждем пока целевой номер не установит звонок (мы будем добавлять его канал в мост)
        logger.debug("Waiting for the target's channel to go 'Up'")

        target_channel = None
        while not target_channel:
            channels_response = send_ami_command('Action: CoreShowChannels\r\n\r\n')
            for line in channels_response.splitlines():
                if f"CallerIDNum: {transfer_to_number}" in line:
                    for chan_line in channels_response.splitlines():
                        if "Channel: " in chan_line:
                            target_channel = chan_line.split(':', 1)[1].strip()
                            break
            if not target_channel:
                time.sleep(1)  # Ждем секунду и повоторяем запрос

        # 3. Добавляем канал целевого номера в мост
        logger.debug(f"Adding target's channel {target_channel} to bridge {bridge_id}")
        add_channel_command_b = (
            f'Action: BridgeAddChannel\r\n'
            f'BridgeUniqueid: {bridge_id}\r\n'
            f'Channel: {target_channel}\r\n'
            f'\r\n'
        )

        add_b_response = send_ami_command(add_channel_command_b)
        logger.debug(f"Add channel to bridge response: {add_b_response}")

        if 'Response: Success' not in add_b_response:
            logger.error(f"Failed to add target's channel to bridge: {add_b_response}")
            raise ValueError("Failed to add target channel to bridge")

        # 4. Теперь ищем активный канал инициатора (A)
        logger.debug(f"Looking for active call for internal number {internal_number}")

        active_channel = None
        while not active_channel:
            channels_response = send_ami_command('Action: CoreShowChannels\r\n\r\n')
            for line in channels_response.splitlines():
                if f"CallerIDNum: {internal_number}" in line:
                    for chan_line in channels_response.splitlines():
                        if "Channel: " in chan_line:
                            active_channel = chan_line.split(':', 1)[1].strip()
                            break
            if not active_channel:
                time.sleep(1)

        # 5. Добавляем активный канал инициатора в мост
        logger.debug(f"Adding initiator's channel {active_channel} to bridge {bridge_id}")
        add_channel_command_a = (
            f'Action: BridgeAddChannel\r\n'
            f'BridgeUniqueid: {bridge_id}\r\n'
            f'Channel: {active_channel}\r\n'
            f'\r\n'
        )

        add_a_response = send_ami_command(add_channel_command_a)
        logger.debug(f"Add initiator's channel to bridge response: {add_a_response}")

        if 'Response: Success' not in add_a_response:
            logger.error(f"Failed to add initiator's channel to bridge: {add_a_response}")
            raise ValueError("Failed to add initiator channel to bridge")

        logger.debug(f"Attended transfer complete. Both parties connected in bridge {bridge_id}")
        
        return {'status': 'success', 'message': f"Attended transfer to {transfer_to_number} completed"}

    except ValueError as e:
        logger.error(f"Error in attended transfer for {internal_number} -> {transfer_to_number}: {str(e)}")
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e
    except Exception as e:
        logger.error(f"General error in attended transfer task: {str(e)}")
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

if __name__ == '__main__':
    logger.debug("Starting Flask application")
    app.run(host="0.0.0.0", port=666, debug=False)