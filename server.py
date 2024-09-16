import time
import threading
import logging
from flask import Flask, request, jsonify
from asterisk.ami import AMIClient, SimpleAction, EventListener

app = Flask(__name__)

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Логирование в файл
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Инициализация клиента AMI для работы с Asterisk
client = AMIClient(address='127.0.0.1', port=5038)
client.login(username='myuser', secret='mypassword')
setivents = SimpleAction('Events',EventMask= 'status,call')
resp = client.send_action(setivents,)
logger.debug(f"Set events on {resp.response.status}")
active_channels = []

# Функция запуска потока для непрерывного прослушивания событий AMI
def start_event_listener():
    def status_event_handler(event, **kwargs):
        global active_channels
        if event.name == 'Status':
            logger.debug(f"Received Status Event: {event}")
            channel = event.get_header('Channel')
            caller_id_num = event.get_header('CallerIDNum')
            context = event.get_header('Context')
            extension = event.get_header('Extension')
            state = event.get_header('ChannelStateDesc')
            active_channels.append({
                "channel": channel,
                "caller_id_num": caller_id_num,
                "context": context,
                "extension": extension,
                "state": state
            })
            logger.info(f"Channel added: {channel} with CallerIDNum: {caller_id_num}")
        else:
            logger.debug(f"New event: {event.name}")
    # Добавляем основной обработчик событий
    client.add_event_listener(EventListener(status_event_handler))
    
    # Проверка событий на стороне клиента
    # while True:
    #     # Ожидание событий
    #     client.poll()

# Запуск прослушивания событий в отдельном потоке.
threading.Thread(target=start_event_listener, daemon=True).start()

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    global active_channels
    active_channels = []  # Очищаем список активных каналов перед запросом
    
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        logger.error(f"Missing parameters: internal_number or transfer_to_number is not provided.")
        return jsonify({"error": "Missing required parameters"}), 400

    # Шаг 1: Отправляем запрос для получения статуса каналов
    action_status = SimpleAction('Status')
    logger.info("Sending AMI Status Action to Asterisk")
    client.send_action(action_status)

    # Ожидаем поступление событий (даем время на получение результатов)
    logger.info("Waiting for events to be handled...")
    time.sleep(5)

    logger.debug(f"Active channels collected: {active_channels}")

    # Поиск нужных каналов
    filtered_channels = [ch for ch in active_channels if internal_number in ch['caller_id_num']]

    if not filtered_channels:
        logger.warning(f"No active call found for internal_number {internal_number}")
        return jsonify({
            "error": "No active call found for this internal number",
            "active_channels": active_channels  # Это для отладки, можно убрать позже
        }), 404

    active_channel = filtered_channels[0]
    channel_name = active_channel['channel']

    # Шаг 2: Проведение перевода вызова
    logger.info(f"Initiating attended transfer from {internal_number} to {transfer_to_number}")
    action_originate = SimpleAction(
        'Originate',
        Channel=channel_name,
        Exten=transfer_to_number,
        Context='from-internal',
        Priority=1,
        CallerID=internal_number,
        Timeout=30000,
        ActionID="AttendedTransfer"
    )

    originate_response = client.send_action(action_originate)

    if originate_response.is_error():
        logger.error(f"Error originating call from {internal_number} to {transfer_to_number}")
        return jsonify({"error": "Failed to initiate call to transfer number"}), 500

    logger.info(f"Attended transfer initiated from {internal_number} to {transfer_to_number} successfully")
    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": originate_response.response.to_dict()
    })

if __name__ == '__main__':
    logger.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5000, debug=False)