import os
import ari
from flask import Flask, request, jsonify

app = Flask(__name__)

# Получаем переменные окружения для Asterisk сервера
AST_SERVER = os.getenv('AST_SERVER', '127.0.0.1')
AST_USER = os.getenv('AST_USER', 'myuser')
AST_PASSWORD = os.getenv('AST_SECRET', 'mypassword')

# Подключаемся к Asterisk через ARI
client = ari.connect(f"http://{AST_SERVER}:8088", AST_USER, AST_PASSWORD)

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json

    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing required parameters"}), 400

    try:
        # Шаг 1. Найти канал по внутреннему номеру
        channels = client.channels.list()
        
        # Найдем канал, где участвует internal_number
        active_channel = None
        for channel in channels:
            if channel.get('caller', {}).get('number') == internal_number:
                active_channel = channel
                break

        if not active_channel:
            return jsonify({"error": "No active channel found for this internal number"}), 404

        # Шаг 2. Создадим новый звонок на transfer_to_number
        new_call = client.channels.originate(
            endpoint=f'SIP/{transfer_to_number}',
            extension=transfer_to_number,
            callerId=internal_number,
            context='from-internal',
            priority=1
        )

        # Шаг 3. Выполним передачу вызова, после ответа на новый звонок
        @client.on_channel_event('StasisStart')
        def on_stasis_start(event, channel):
            if channel.id == new_call.id:
                # Новый звонок поднят, выполняем attended transfer
                client.channels.bridge(active_channel.id, channel.id)
                return jsonify({"success": True, "message": "Attended transfer completed"})

        client.run(app)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=666,debug=False)