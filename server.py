from flask import Flask, request, jsonify
from asterisk.ami import AMIClient, SimpleAction, EventListener
import time
app = Flask(__name__)

# Настройка Asterisk AMI клиента
client = AMIClient(address='127.0.0.1', port=5038,timeout=None)
client.login(username='myuser', secret='mypassword')

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    data = request.json

    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing required parameters"}), 400

    # Шаг 1. Найдем активный канал по внутреннему номеру
    action_status = SimpleAction(
        'Status',
    )
    response = client.send_action(action_status).response
    time.sleep(3)
    return jsonify(vars(response))
    # if  response.response:
    #     return str(type(response.response))
    channels = [channel.get_header('Channel') for channel in response.response if internal_number in channel.get_header('Channel')]

    if not channels:
        return jsonify({"error": "No active call found for this internal number"}), 404
    
    # Взять первый активный канал (если их несколько, можно выбрать логику для правильного выбора)
    active_channel = channels[0]

    # Шаг 2. Выполним Dial для нового номера
    action_originate = SimpleAction(
        'Originate',
        Channel=active_channel,
        Exten=transfer_to_number,
        Context='from-internal',
        Priority=1,
        CallerID=internal_number,
        Timeout=30000,  # timeout in milliseconds
        ActionID="AttendedTransfer"
    )

    originate_response = client.send_action(action_originate)

    if originate_response.response.is_error():
        return jsonify({"error": "Failed to initiate call to transfer number"}), 500

    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": originate_response.response.to_dict()
    })
@app.route('/api/debug_status', methods=['GET'])
def debug_status():
    # Send the 'Status' action request to Asterisk AMI
    action_status = SimpleAction('Status')
    response = client.send_action(action_status)

    # Convert the response to a dictionary (for JSON conversion)
    try:
        response_dict = response.response.to_dict()  # Make sure the response can be converted to a dict
    except AttributeError:
        # If an error occurs (e.g., no to_dict method), handle it gracefully and inspect manually
        response_dict = {"error": "Unable to convert response to dictionary format"}

    # Return the response as JSON (for debugging purposes)
    return jsonify(response_dict)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)