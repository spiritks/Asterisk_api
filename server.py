import time
import logging
from flask import Flask, request, jsonify
from asterisk.ami import AMIClient, SimpleAction, EventListener

# Инициализация Flask приложения
app = Flask(__name__)

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Уровень логирования (DEBUG для максимальной детализации)

# Логирование в файл
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.DEBUG)

# Логирование в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Формат сообщений
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Добавляем хендлеры к логгеру
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Список активных каналов
active_channels = []
logger.debug("Starting Flask app...")
# Настройка Asterisk AMI клиента
client = AMIClient(address='127.0.0.1', port=5038)
client.login(username='myuser', secret='mypassword')

# Функция обработки событий 'Status'
def status_event_handler(event, **kwargs):
    global active_channels

    # Логирование всех полученных событий
    logger.debug(f"Event received: {event.name}")

    if event.name == 'Status':
        # Логируем детали события 'Status'
        logger.debug(f"Details of Status event: {event}")

        # Извлечение данных статуса
        channel = event.get_header('Channel')
        caller_id_num = event.get_header('CallerIDNum')
        context = event.get_header('Context')
        extension = event.get_header('Extension')
        state = event.get_header('ChannelStateDesc')

        # Логируем данные канала
        logger.info(f"Processing Channel: {channel}, CallerIDNum: {caller_id_num}, Context: {context}, Extension: {extension}, State: {state}")

        # Добавляем канал в active_channels
        active_channels.append({
            "channel": channel,
            "caller_id_num": caller_id_num,
            "context": context,
            "extension": extension,
            "state": state
        })

# Забираем события в AMI
client.add_event_listener(EventListener(status_event_handler))

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    global active_channels
    active_channels = []  # Очищаем список активных каналов перед новым запросом
    
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        logger.error(f"Missing parameters: internal_number or transfer_to_number is not provided.")
        return jsonify({"error": "Missing required parameters"}), 400

    # Шаг 1. Отправляем запрос Action 'Status'
    action_status = SimpleAction('Status')
    logger.info("Sending AMI Status Action to Asterisk")
    response = client.send_action(action_status)

    # Ждем время для обработки ивентов
    logger.info("Waiting for events to be handled...")
    time.sleep(5)  # Время ожидания событий для демонстрации

    # Отладочный вывод активных каналов
    logger.debug(f"Active channels collected: {active_channels}")

    # Поиск активных каналов по внутреннему номеру
    filtered_channels = [ch for ch in active_channels if internal_number in ch['caller_id_num']]

    if not filtered_channels:
        logger.warning(f"No active call found for internal_number {internal_number}")
        return jsonify({
            "error": "No active call found for this internal number",
            "active_channels": active_channels  # Это для отладки, можно убрать в финальной версии
        }), 404

    # Берем первый подходящий канал
    active_channel = filtered_channels[0]
    channel_name = active_channel['channel']

    # Шаг 2. Осуществляем трансфер с помощью вызова Originate
    logger.info(f"Initiating attended transfer from {internal_number} to {transfer_to_number}")
    action_originate = SimpleAction(
        'Originate',
        Channel=channel_name,            
        Exten=transfer_to_number,
        Context='from-internal',
        Priority=1,
        CallerID=internal_number,
        Timeout=30000,  # 30 секунд
        ActionID="AttendedTransfer"
    )

    originate_response = client.send_action(action_originate)

    # Проверка на ошибку в ответе originate
    if originate_response.is_error():
        logger.error(f"Error originating call from {internal_number} to {transfer_to_number}")
        return jsonify({"error": "Failed to initiate call to transfer number"}), 500

    # В случае успеха
    logger.info(f"Attended transfer initiated from {internal_number} to {transfer_to_number} successfully")
    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": originate_response.response.to_dict()
    })

if __name__ == '__main__':
    logger.info("Starting Flask app...")
    app.run(host='0.0.0.0', port=5000, debug=False)