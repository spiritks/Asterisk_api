from flask import Flask, request, jsonify
from asterisk.ami import AMIClient, SimpleAction, EventListener
import time

app = Flask(__name__)

# Asterisk AMI Client configuration
client = AMIClient(address='127.0.0.1', port=5038)
client.login(username='myuser', secret='mypassword')
print("App started")
active_channels = []

# Logs all events, focusing on Status events
def status_event_handler(event, **kwargs):
    global active_channels

    # Print the event to see what's received
    print(f"Event: {event.name}")

    # We're specifically looking for 'Status' events
    if event.name == 'Status':
        print(f"Received Status Event: {event}")  # Debugging: Print entire event for inspection

        # Extract details from the Status event
        channel = event.get_header('Channel')
        caller_id_num = event.get_header('CallerIDNum')
        context = event.get_header('Context')
        extension = event.get_header('Extension')
        state = event.get_header('ChannelStateDesc')

        # Print the channel details for debugging
        print(f"Channel: {channel}, CallerIDNum: {caller_id_num}, Context: {context}, Extension: {extension}, State: {state}")

        # Append channel info to active_channels if it's valid
        active_channels.append({
            "channel": channel,
            "caller_id_num": caller_id_num,
            "context": context,
            "extension": extension,
            "state": state
        })

# Add event listener to capture AMI Status events
client.add_event_listener(EventListener(status_event_handler))

@app.route('/api/attended_transfer', methods=['POST'])
def attended_transfer():
    global active_channels
    active_channels = []  # Reset active channels list for new requests
    
    data = request.json
    internal_number = data.get('internal_number')
    transfer_to_number = data.get('transfer_to_number')

    if not internal_number or not transfer_to_number:
        return jsonify({"error": "Missing required parameters"}), 400

    # Step 1: Send Status request to find the active channel
    action_status = SimpleAction('Status')
    response = client.send_action(action_status)

    # Manually sleep to allow time for Status events to be processed
    time.sleep(5)  # Increase sleep time for debugging, should be dynamic

    # Debugging print to see all captured channels
    print(f"Active channels after timeout: {active_channels}")

    # Filter based on internal number
    filtered_channels = [ch for ch in active_channels if internal_number in ch['caller_id_num']]

    if not filtered_channels:
        return jsonify({
            "error": "No active call found for this internal number",
            "active_channels": active_channels  # Return active channels for debugging purposes
        }), 404
    
    # Get the first channel from the filtered list
    active_channel = filtered_channels[0]
    channel_name = active_channel['channel']

    # Step 2: Originate a new call (attended transfer)
    action_originate = SimpleAction(
        'Originate',
        Channel=channel_name,            # Use the found active channel
        Exten=transfer_to_number,
        Context='from-internal',
        Priority=1,
        CallerID=internal_number,
        Timeout=30000,  # 30 seconds timeout
        ActionID="AttendedTransfer"
    )

    originate_response = client.send_action(action_originate)

    if originate_response.is_error():
        return jsonify({"error": "Failed to initiate call to transfer number"}), 500

    # Success response
    return jsonify({
        "success": True,
        "message": f"Attended transfer initiated from {internal_number} to {transfer_to_number}",
        "details": originate_response.response.to_dict()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)