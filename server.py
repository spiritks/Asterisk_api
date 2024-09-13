import time
from flask import Flask, request, jsonify
from asterisk.ami import AMIClient, SimpleAction, EventListener

app = Flask(__name__)

# Asterisk AMI Client configuration
client = AMIClient(address='127.0.0.1', port=5038)
client.login(username='myuser', secret='mypassword')

active_channels = []

# Define a function to handle status events
def status_event_handler(event, **kwargs):
    global active_channels

    # Filter for 'Status' events and extract the relevant information
    if event.name == 'Status':
        channel = event.get_header('Channel')
        caller_id_num = event.get_header('CallerIDNum')
        context = event.get_header('Context')
        extension = event.get_header('Extension')
        state = event.get_header('ChannelStateDesc')
        
        # Store pertinent status info about this event in the active channels list
        active_channels.append({
            "channel": channel,
            "caller_id_num": caller_id_num,
            "context": context,
            "extension": extension,
            "state": state
        })

# Add the event listener
client.add_event_listener(EventListener(status_event_handler))

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    global active_channels
    active_channels = []  # Clear the active channels before every new request
    
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing required parameters"}), 400

    # Step 1: Find the active channel
    action_status = SimpleAction('Status')
    response = client.send_action(action_status)

    # Manually sleep after sending the Status action to allow time for EventListener to gather events
    time.sleep(1)  # Wait for 3 seconds (adjust this if needed)

    # Now, filter the channels for the internal number
    filtered_channels = [ch for ch in active_channels if internal_number in ch['caller_id_num']]

    if not filtered_channels:
        return jsonify({"error": "No active call found for this internal number","active_channels": active_channels}), 404
    
    # Get the first match
    active_channel = filtered_channels[0]
    channel_name = active_channel['channel']

    # Step 2: Initiate an attended transfer
    action_originate = SimpleAction(
        'Originate',
        Channel=channel_name,            # Use the found active channel
        Exten=transfer_to_number,
        Context='from-internal',
        Priority=1,
        CallerID=internal_number,
        Timeout=30000,  # timeout in milliseconds
        ActionID="AttendedTransfer"
    )

    originate_response = client.send_action(action_originate)

    # Check for error in originating transfer
    if originate_response.is_error():
        return jsonify({"error": "Failed to initiate call to transfer number"}), 500

    # Success response if transfer is initiated
    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": originate_response.response.to_dict()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)