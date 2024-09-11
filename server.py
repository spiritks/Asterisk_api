import os
from flask import Flask, request, jsonify
from asterisk_ami import AMIClient

app = Flask(__name__)

# Получаем настройки из переменных окружения
AST_SERVER = os.getenv('AST_SERVER', '127.0.0.1')    # По умолчанию 127.0.0.1
AST_PORT = int(os.getenv('AST_PORT', 5038))          # По умолчанию 5038
AST_USER = os.getenv('AST_USER', 'myuser')
AST_SECRET = os.getenv('AST_SECRET', 'mypassword')

# Настройка Asterisk AMI клиента с использованием переменных окружения
client = AMIClient(address=AST_SERVER, port=AST_PORT)
client.login(username=AST_USER, secret=AST_SECRET)

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json

    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing required parameters"}), 400

    # Шаг 1. Найдем активный канал по внутреннему номеру
    status_response = client.send_action({
        'action': 'Status'
    })

    channels = [channel['Channel'] for channel in status_response.get('response') if internal_number in channel.get('CallerIDNum', '')]

    if not channels:
        return jsonify({"error": "No active call found for this internal number"}), 404

    # Взять первый активный канал (это канал звонка для данного абонента)
    active_channel = channels[0]

    # Шаг 2. Выполним Redirect (Dial для нового номера на активном канале)
    redirect_response = client.send_action({
        'action': 'Redirect',
        'Channel': active_channel,
        'Exten': transfer_to_number,
        'Context': 'from-internal',
        'Priority': 1
    })

    if redirect_response.get('response') != 'Success':
        return jsonify({"error": "Failed to initiate attended transfer"}), 500

    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": redirect_response
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)